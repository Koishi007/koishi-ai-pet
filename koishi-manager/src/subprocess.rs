use std::{
    ffi::OsStr,
    process::{Command, Output, Stdio},
    time::Duration,
};

use indicatif::{ProgressBar, ProgressStyle};
use owo_colors::OwoColorize;

use crate::errors::{Result, ToIoErrors};

fn make_uv(pb: &ProgressBar) -> Result<()> {
    let output = Command::new("uv")
        .arg("--version")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();

    let new = match output {
        Ok(output) => {
            !output.status.success()
                && !String::from_utf8(output.stdout)
                    .unwrap_or(String::new())
                    .contains("uv")
        }
        Err(_) => true,
    };

    if new {
        pb.println(format!(
            "{} {}",
            ">".bright_magenta().bold(),
            "初次使用，我们将自动为你安装高速包管理器 uv！这可能需要几分钟...".bright_blue()
        ));
        let result = install_uv();
        match result {
            Ok(_) => pb.println(format!(
                "{} {}",
                "✓".bright_green().bold(),
                "高速包管理器 uv 安装成功！".bright_blue()
            )),
            Err(err) => {
                pb.println(format!(
                    "{} {}\n{} {}",
                    "×".bright_red().bold(),
                    "安装失败！".bright_red(),
                    "╰──>".bright_red().dimmed(),
                    err.bright_red().dimmed()
                ));
                return Err(err);
            }
        }
    }

    Ok(())
}

fn install_uv() -> Result<Output> {
    use std::os::windows::process::CommandExt;

    Ok(Command::new("powershell")
        .arg("-ExecutionPolicy")
        .arg("ByPass")
        .arg("-Command")
        .raw_arg(r#""irm https://astral.sh/uv/install.ps1 | iex""#)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .unwrap_packman(String::from("请尝试手动用 powershell -ExecutionPolicy ByPass -Command irm https://astral.sh/uv/install.ps1 | iex 手动安装；也可以从 https://github.com/astral-sh/uv/releases/ 自行下载合适的 uv 并将其下载路经添加至环境变量。"))?)
}

pub(crate) fn call<I, S>(name: &str, args: I, help: String) -> Result<Output>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let output = Command::new(name)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .unwrap_packman(help)?;

    Ok(output)
}

pub(crate) fn call_uv<I, S>(args: I, pb: &ProgressBar, help: String) -> Result<Output>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    make_uv(pb)?;
    call("uv", args, help)
}

pub(crate) fn setup_spinner(pb: ProgressBar) -> ProgressBar {
    pb.enable_steady_tick(Duration::from_millis(100));
    pb.set_style(
        ProgressStyle::default_spinner()
            .tick_strings(&["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
            .template("{spinner:.blue} {msg}")
            .unwrap(),
    );
    pb
}

pub(crate) fn setup_bar(pb: ProgressBar) -> ProgressBar {
    pb.set_style(
        ProgressStyle::default_bar()
            .template("{msg} {bar:30.green/blue} {pos}/{len}")
            .unwrap()
            .progress_chars("━━"),
    );
    pb
}

#[cfg(test)]
mod test {
    use std::{fs, path::PathBuf, str::FromStr};

    use indicatif::ProgressBar;

    use super::*;

    fn get_uv_path() -> PathBuf {
        let user = env!("USERPROFILE");
        PathBuf::from_str(user)
            .unwrap()
            .join(".local")
            .join("bin")
            .join("uv.exe")
    }

    #[test]
    fn test_install_uv_when_uv_exists() {
        let pb = ProgressBar::new_spinner();
        let uv_path = get_uv_path();
        let old = fs::read(&uv_path).unwrap();
        make_uv(&pb).unwrap();
        let new = fs::read(&uv_path).unwrap();
        assert_eq!(old, new);
    }

    #[test]
    fn test_will_install_uv_make_uv() {
        let uv_path = get_uv_path();
        let _ = fs::remove_file(&uv_path);
        install_uv().unwrap();
        assert!(fs::exists(&uv_path).unwrap());
    }

    #[test]
    fn test_make_uv_when_uv_does_not_exist() {
        let pb = ProgressBar::new_spinner();
        let uv_path = get_uv_path();
        let _ = fs::remove_file(&uv_path);
        make_uv(&pb).unwrap();
        assert!(fs::exists(&uv_path).unwrap());
    }

    #[test]
    fn test_make_uv_when_uv_exists() {
        let pb = ProgressBar::new_spinner();
        let uv_path = get_uv_path();
        let old = fs::read(&uv_path).unwrap();
        make_uv(&pb).unwrap();
        let new = fs::read(&uv_path).unwrap();
        assert_eq!(old, new);
    }

    #[test]
    fn test_call() {
        let output = call("powershell", ["echo", "1"], String::new()).unwrap();
        let output = String::from_utf8(output.stdout).unwrap();
        assert_eq!(output.trim(), "1");
    }

    #[test]
    fn test_call_uv() {
        let pb = ProgressBar::new_spinner();
        let output = call_uv(["--version"], &pb, String::new()).unwrap();
        let output = String::from_utf8(output.stdout).unwrap();
        assert!(output.contains("uv"));
    }

    #[test]
    fn test_will_call_uv_make_uv() {
        let pb = ProgressBar::new_spinner();
        let uv_path = get_uv_path();
        let _ = fs::remove_file(&uv_path);
        call_uv(["--version"], &pb, String::new()).unwrap();
        assert!(fs::exists(&uv_path).unwrap());
    }

    // NOTE: 彩色输出与样式并无测试必要，不影响程序主要运行，若有 bug 指出并修改即可
}
