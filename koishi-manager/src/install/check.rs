use std::fs;

use indicatif::ProgressBar;

use owo_colors::OwoColorize;

use crate::{
    errors::{Errors::Check, Result},
    subprocess::{call, call_uv, setup_bar},
};

pub fn process() -> Result<()> {
    let pb = ProgressBar::new(5);
    pb.set_message("少女检查中...");
    let pb = setup_bar(pb);

    let results = [
        validate(&pb, check_venv(), "虚拟环境检查", Failure::Venv),
        validate(&pb, check_python(), "Python 安装检查", Failure::Python),
        validate(&pb, check_deps(&pb), "依赖项检查", Failure::Deps),
        validate(&pb, check_entry(), "程序主入口检查", Failure::Entry),
    ];
    let errors: Vec<_> = results.iter().flatten().cloned().collect();

    if errors.len() > 0 {
        return Err(Check(errors, String::from("尝试根据上方的提示进行修复，并保证你在项目根目录运行此文件")));
    }

    pb.finish_and_clear();

    Ok(())
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Failure {
    Venv,
    Python,
    Deps,
    Entry,
}

fn validate(
    pb: &ProgressBar,
    rst: std::result::Result<(), String>,
    name: &str,
    typ: Failure,
) -> Option<(Failure, String)> {
    let failure = validate_result(rst, typ);

    match &failure {
        Some((_, err)) => {
            pb.println(format!(
                "{} {}{}",
                "×".bright_red().bold(),
                name.bright_red(),
                "失败！".bright_red()
            ));
            pb.println(format!(
                "{} {}",
                "╰──>".bright_red().dimmed(),
                err.bright_red().dimmed()
            ));
        }
        None => {
            pb.println(format!(
                "{} {}{}",
                "✓".green().bold(),
                name.bright_blue(),
                "检查通过".bright_blue()
            ));
            pb.inc(1);
        }
    }

    failure
}

fn validate_result(
    rst: std::result::Result<(), String>,
    typ: Failure,
) -> Option<(Failure, String)> {
    match rst {
        Ok(()) => None,
        Err(err) => Some((typ, err)),
    }
}

fn check_python() -> std::result::Result<(), String> {
    let version = call(
        "venv/Scripts/python.exe",
        ["--version"],
        String::from("尝试切换到能够正常允许子进程的环境运行此程序"),
    );

    if let Err(err) = version {
        return Err(format!("调用 Python 失败：{}", err));
    }

    let version = String::from_utf8(version.unwrap().stdout);
    if let Err(err) = version {
        return Err(format!("输出解析失败：{}", err));
    }

    let version = version.unwrap();
    if !python_output_ok(&version) {
        return Err(format!("输出不符合预期：{}", version));
    }

    Ok(())
}

fn python_output_ok(output: &str) -> bool {
    output.contains("Python") && output.contains("3.1")
}

fn check_venv() -> std::result::Result<(), String> {
    check_venv_at("venv")
}

fn check_venv_at(root: &str) -> std::result::Result<(), String> {
    if let Some(err) = check_exist(root, "venv 目录不存在！") {
        return Err(err);
    }
    if let Some(err) = check_exist(&format!("{root}/Lib"), "虚拟环境缺失 Lib 目录") {
        return Err(err);
    }
    if let Some(err) = check_exist(
        &format!("{root}/Lib/site-packages"),
        "虚拟环境缺失 Lib 目录",
    ) {
        return Err(err);
    }
    if let Some(err) = check_exist(&format!("{root}/Scripts"), "虚拟环境缺失 Scripts 目录")
    {
        return Err(err);
    }

    Ok(())
}

fn check_deps(pb: &ProgressBar) -> std::result::Result<(), String> {
    let output = call_uv(
        ["pip", "list", "-p", "venv/Scripts/python.exe"],
        pb,
        String::from("尝试切换到能够正常允许子进程的环境运行此程序"),
    );

    if let Err(err) = output {
        return Err(format!("调用 uv 失败：{}", err));
    }

    let output = String::from_utf8(output.unwrap().stdout);
    if let Err(err) = output {
        return Err(format!("输出解析失败：{}", err));
    }

    let output = output.unwrap();
    // NOTE: 我们在这里只检查 koishi-ai-pet 的安装情况，因为只要 koishi-ai-pet 安装成功，其余依赖也理应安装成功
    // 注：出于虚拟环境和包名与导入名的特性，uv pip list 无法准确列出所有依赖，因此无法准确处理所有依赖检查，故只检查项目本体
    if !deps_output_ok(&output) {
        return Err(format!("输出不符合预期：{}", output));
    }

    Ok(())
}

fn deps_output_ok(output: &str) -> bool {
    output.contains("koishi-ai-pet")
}

fn check_entry() -> std::result::Result<(), String> {
    check_entry_at("venv/Scripts/koishi.exe")
}

fn check_entry_at(entry: &str) -> std::result::Result<(), String> {
    if let Some(err) = check_exist(entry, "程序主入口缺失") {
        return Err(err);
    }
    Ok(())
}

fn check_exist(dir: &str, info: &str) -> Option<String> {
    let dir = fs::exists(dir);
    if dir.is_err() || !dir.unwrap() {
        return Some(String::from(info));
    }

    None
}

#[cfg(test)]
mod test {
    use std::{
        fs,
        path::{Path, PathBuf},
    };

    use super::*;

    fn temp_dir(tag: &str) -> PathBuf {
        std::env::temp_dir().join(format!("koishi-check-{tag}-{}", std::process::id()))
    }

    fn cleanup(path: &Path) {
        let _ = fs::remove_dir_all(path);
    }

    #[test]
    fn test_validate_result_success() {
        assert_eq!(validate_result(Ok(()), Failure::Venv), None);
    }

    #[test]
    fn test_validate_result_failure() {
        assert_eq!(
            validate_result(Err(String::from("boom")), Failure::Deps),
            Some((Failure::Deps, String::from("boom")))
        );
    }

    #[test]
    fn test_validate_result_preserves_failure_type() {
        for typ in [
            Failure::Venv,
            Failure::Python,
            Failure::Deps,
            Failure::Entry,
        ] {
            assert_eq!(
                validate_result(Err(String::from("err")), typ.clone()),
                Some((typ, String::from("err")))
            );
        }
    }

    #[test]
    fn test_check_exist_found_dir() {
        let dir = temp_dir("exist-found");
        fs::create_dir_all(&dir).unwrap();
        assert_eq!(check_exist(dir.to_str().unwrap(), "缺失"), None);
        cleanup(&dir);
    }

    #[test]
    fn test_check_exist_found_file() {
        let dir = temp_dir("exist-file");
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("a.txt");
        fs::write(&file, b"").unwrap();
        assert_eq!(check_exist(file.to_str().unwrap(), "缺失"), None);
        cleanup(&dir);
    }

    #[test]
    fn test_check_exist_missing() {
        let dir = temp_dir("exist-missing");
        cleanup(&dir);
        assert_eq!(
            check_exist(dir.to_str().unwrap(), "缺失"),
            Some(String::from("缺失"))
        );
    }

    #[test]
    fn test_check_venv_at_complete() {
        let root = temp_dir("venv-complete");
        fs::create_dir_all(root.join("Lib/site-packages")).unwrap();
        fs::create_dir_all(root.join("Scripts")).unwrap();

        assert_eq!(check_venv_at(root.to_str().unwrap()), Ok(()));

        cleanup(&root);
    }

    #[test]
    fn test_check_venv_at_missing_root() {
        let root = temp_dir("venv-missing-root");
        cleanup(&root);

        assert!(check_venv_at(root.to_str().unwrap()).is_err());

        cleanup(&root);
    }

    #[test]
    fn test_check_venv_at_missing_lib() {
        let root = temp_dir("venv-missing-lib");
        fs::create_dir_all(&root).unwrap();

        let err = check_venv_at(root.to_str().unwrap()).unwrap_err();
        assert!(err.contains("Lib"));

        cleanup(&root);
    }

    #[test]
    fn test_check_venv_at_missing_site_packages() {
        let root = temp_dir("venv-missing-sp");
        fs::create_dir_all(root.join("Lib")).unwrap();
        fs::create_dir_all(root.join("Scripts")).unwrap();

        let err = check_venv_at(root.to_str().unwrap()).unwrap_err();
        assert!(err.contains("Lib"));

        cleanup(&root);
    }

    #[test]
    fn test_check_venv_at_missing_scripts() {
        let root = temp_dir("venv-missing-scripts");
        fs::create_dir_all(root.join("Lib/site-packages")).unwrap();

        let err = check_venv_at(root.to_str().unwrap()).unwrap_err();
        assert!(err.contains("Scripts"));

        cleanup(&root);
    }

    #[test]
    fn test_check_entry_at_found() {
        let root = temp_dir("entry-found");
        fs::create_dir_all(&root).unwrap();
        let entry = root.join("koishi.exe");
        fs::write(&entry, b"").unwrap();

        assert_eq!(check_entry_at(entry.to_str().unwrap()), Ok(()));

        cleanup(&root);
    }

    #[test]
    fn test_check_entry_at_missing() {
        let root = temp_dir("entry-missing");
        fs::create_dir_all(&root).unwrap();
        let entry = root.join("koishi.exe");

        let err = check_entry_at(entry.to_str().unwrap()).unwrap_err();
        assert_eq!(err, "程序主入口缺失");

        cleanup(&root);
    }

    #[test]
    fn test_python_output_ok_accepts_31x() {
        assert!(python_output_ok("Python 3.13.1"));
        assert!(python_output_ok("Python 3.11.9"));
        assert!(python_output_ok("Python 3.12.4"));
    }

    #[test]
    fn test_python_output_ok_rejects_missing_prefix() {
        assert!(!python_output_ok("3.13.1"));
        assert!(!python_output_ok(""));
        assert!(!python_output_ok("  "));
    }

    #[test]
    fn test_python_output_ok_rejects_wrong_version() {
        assert!(!python_output_ok("Python 2.7.18"));
        assert!(!python_output_ok("Python 3.0.1"));
        assert!(!python_output_ok("pypy 3.13.1"));
    }

    #[test]
    fn test_deps_output_ok_found() {
        assert!(deps_output_ok("koishi-ai-pet 0.1.0"));
        assert!(deps_output_ok(
            "Package           Version\nkoishi-ai-pet 0.1.0\nuvicorn 0.30.0"
        ));
    }

    #[test]
    fn test_deps_output_ok_missing() {
        assert!(!deps_output_ok(""));
        assert!(!deps_output_ok("uvicorn 0.30.0"));
    }

    // NOTE: 我们只测试最关键部分，其余关于环境操作的测试取决于 subprocess 是否正常运作，而我们已经在 subprocess 里进行了详细的测试
    //       （其实是懒得写 fumofumo (ᗜ ˰ ᗜ) ）
}
