use std::{fs, io, process::Output};

use indicatif::ProgressBar;
use owo_colors::OwoColorize;

use crate::{
    errors::{Result, ToIoErrors},
    subprocess::{call, call_uv, setup_spinner},
};

const LOWEST: (i8, i8, i8) = (3, 11, 0);
const HIGHTEST: (i8, i8, i8) = (3, 14, 0);

pub fn process() -> Result<()> {
    let pb = ProgressBar::new_spinner();
    pb.set_message("正在创建虚拟环境...");
    let pb = setup_spinner(pb);

    // 检查 Python 安装情况
    let mut python = detect_python()?;

    if let Some(path) = &python {
        pb.println(format!(
            "{} {} {}",
            ">".bright_magenta().bold(),
            "发现可用的 Python：".bright_blue(),
            path.dimmed()
        ));
    } else {
        // 无 Python 自动安装
        pb.println(format!(
            "{} {}",
            ">".bright_magenta().bold(),
            "未发现可用的 Python，正在自动安装...".bright_blue()
        ));
        install_python(&pb)?;
        python = Some(String::from("3.13"));
    }

    // 创建虚拟环境
    create_venv(&pb, &python.unwrap_or_else(|| String::from("3.13")))?;

    pb.finish_and_clear();
    eprintln!(
        "{} {}",
        "✓".bright_green().bold(),
        "虚拟环境创建成功".bright_blue()
    );

    Ok(())
}

fn detect_python() -> Result<Option<String>> {
    let help = String::from("尝试切换到能够正常允许子进程的环境运行此程序");

    let output = call(
        "python",
        ["-c", r#"import sys; print(sys.executable)"#],
        help.clone(),
    )?;
    let version = call("python", ["--version"], help)?;

    Ok(python_from_output(&output, &version))
}

fn python_from_output(output: &Output, version: &Output) -> Option<String> {
    if !output.status.success() {
        return None;
    }

    let path = String::from_utf8(output.stdout.clone()).ok()?;
    let version = String::from_utf8(version.stdout.clone()).ok()?;

    parse_version(version.trim())
        .filter(|&v| in_supported_range(v))
        .map(|_| path.trim().to_string())
}

fn parse_version(version: &str) -> Option<(i8, i8, i8)> {
    let version = version.strip_prefix("Python")?.trim();

    let mut parts = version.split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next()?.parse().ok()?;
    let patch = parts.next()?.parse().ok()?;

    if parts.next().is_some() {
        return None;
    }

    Some((major, minor, patch))
}

fn in_supported_range(version: (i8, i8, i8)) -> bool {
    LOWEST <= version && version < HIGHTEST
}

fn install_python(pb: &ProgressBar) -> Result<()> {
    call_uv(
        [
            "python",
            "install",
            // XXX: 能否用 cpython-3.13.13-windows-x86_64-none 替换此处的 3.13？
            "3.13",
            "--mirror",
            "https://pypi.tuna.tsinghua.edu.cn/simple",
        ],
        pb,
        String::from(
            "请尝试手动用 uv python install 3.13 --mirror https://pypi.tuna.tsinghua.edu.cn/simple，或更换或不指定镜像源，或在官网下载 Python 3.13（x64 无 free-threading 版本）并将其下载路经添加至环境变量。",
        ),
    )?;
    pb.println(format!(
        "{} {}",
        "✓".bright_green().bold(),
        "Python 3.13 自动安装成功！正在继续创建虚拟环境...".bright_blue()
    ));

    Ok(())
}

fn create_venv(pb: &ProgressBar, python: &str) -> Result<()> {
    let rst = fs::create_dir("venv");
    if let Err(err) = &rst {
        if err.kind() != io::ErrorKind::AlreadyExists {
            rst.unwrap_io(String::from("请尝试手动创建 venv 目录。"))?
        }
    }
    call_uv(
        ["venv", "-c", "-p", python, "venv"],
        pb,
        String::from("请尝试手动用 uv venv -p 3.13 或 python -m venv venv 创建虚拟环境"),
    )?;

    Ok(())
}

#[cfg(test)]
mod test {
    use std::process::{Command, Output};

    use super::*;

    fn output(success: bool, stdout: &str) -> Output {
        let status = Command::new("cmd")
            .args(["/C", if success { "exit 0" } else { "exit 1" }])
            .status()
            .unwrap();
        Output {
            status,
            stdout: stdout.as_bytes().to_vec(),
            stderr: Vec::new(),
        }
    }

    #[test]
    fn test_parse_version_normal() {
        assert_eq!(parse_version("Python 3.13.1"), Some((3, 13, 1)));
        assert_eq!(parse_version("Python 3.11.0"), Some((3, 11, 0)));
    }

    #[test]
    fn test_parse_version_trims_whitespace() {
        assert_eq!(parse_version("Python 3.13.1\n"), Some((3, 13, 1)));
        assert_eq!(parse_version("Python 3.13.1"), Some((3, 13, 1)));
        assert_eq!(parse_version("Python   3.13.1  "), Some((3, 13, 1)));
    }

    #[test]
    fn test_parse_version_missing_prefix() {
        assert_eq!(parse_version("3.13.1"), None);
    }

    #[test]
    fn test_parse_version_incomplete() {
        assert_eq!(parse_version("Python 3.13"), None);
        assert_eq!(parse_version("Python 3"), None);
        assert_eq!(parse_version("Python"), None);
        assert_eq!(parse_version(""), None);
    }

    #[test]
    fn test_parse_version_extra_part() {
        assert_eq!(parse_version("Python 3.13.1.2"), None);
    }

    #[test]
    fn test_parse_version_non_numeric() {
        assert_eq!(parse_version("Python 3.a.1"), None);
        assert_eq!(parse_version("Python 3.13.1rc1"), None);
    }

    #[test]
    fn test_in_supported_range_lowest_inclusive() {
        assert!(in_supported_range((3, 11, 0)));
        assert!(in_supported_range((3, 11, 99)));
        assert!(in_supported_range((3, 12, 0)));
        assert!(in_supported_range((3, 13, 99)));
    }

    #[test]
    fn test_in_supported_range_highest_exclusive() {
        assert!(!in_supported_range((3, 14, 0)));
        assert!(!in_supported_range((3, 14, 1)));
        assert!(!in_supported_range((3, 15, 0)));
    }

    #[test]
    fn test_in_supported_range_too_old_or_new() {
        assert!(!in_supported_range((3, 10, 99)));
        assert!(!in_supported_range((2, 13, 1)));
        assert!(!in_supported_range((4, 0, 0)));
    }

    #[test]
    fn test_python_from_output_success() {
        let out = output(true, "C:\\Python313\\python.exe");
        let ver = output(true, "Python 3.13.1");
        assert_eq!(
            python_from_output(&out, &ver),
            Some(String::from("C:\\Python313\\python.exe"))
        );
    }

    #[test]
    fn test_python_from_output_failed_status() {
        let out = output(false, "");
        let ver = output(true, "Python 3.13.1");
        assert_eq!(python_from_output(&out, &ver), None);
    }

    #[test]
    fn test_python_from_output_version_failed_status() {
        let out = output(true, "C:\\Python313\\python.exe");
        let ver = output(false, "");
        assert_eq!(python_from_output(&out, &ver), None);
    }

    #[test]
    fn test_python_from_output_unsupported_version() {
        let out = output(true, "C:\\Python310\\python.exe");
        let ver = output(true, "Python 3.10.11");
        assert_eq!(python_from_output(&out, &ver), None);
    }

    #[test]
    fn test_python_from_output_bad_version_format() {
        let out = output(true, "C:\\Python313\\python.exe");
        let ver = output(true, "3.13.1");
        assert_eq!(python_from_output(&out, &ver), None);
    }

    #[test]
    fn test_python_from_output_non_utf8_stdout() {
        let status = Command::new("cmd").args(["/C", "exit 0"]).status().unwrap();
        let out = Output {
            status,
            stdout: vec![0xFF, 0xFE],
            stderr: Vec::new(),
        };
        let ver = output(true, "Python 3.13.1");
        assert_eq!(python_from_output(&out, &ver), None);
    }

    // NOTE: 我们只测试最关键部分，其余关于环境操作的测试取决于 subprocess 是否正常运作，而我们已经在 subprocess 里进行了详细的测试
    //       （其实是懒得写 fumofumo (ᗜ ˰ ᗜ) ）
}
