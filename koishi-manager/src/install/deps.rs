use std::fs;

use indicatif::ProgressBar;
use owo_colors::OwoColorize;

use crate::{
    errors::{Errors::Packman, Result},
    subprocess::{call_uv, setup_spinner},
};

const PYPROJECT: &str = "pyproject.toml";
const DEPS_UV_ARGS: [&str; 8] = [
    "pip",
    "install",
    "-e",
    ".",
    "--default-index",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "-p",
    "venv/Scripts/python.exe",
];

pub fn process() -> Result<()> {
    check_pyproject()?;

    let pb = ProgressBar::new_spinner();
    pb.set_message("正在安装依赖，这可能会花费几分钟...");
    let pb = setup_spinner(pb);

    install_deps(&pb)?;

    pb.finish_and_clear();
    eprintln!(
        "{} {}",
        "✓".bright_green().bold(),
        "依赖安装成功".bright_blue()
    );

    Ok(())
}

fn check_pyproject() -> Result<()> {
    check_pyproject_at(PYPROJECT)
}

fn check_pyproject_at(path: &str) -> Result<()> {
    if let Ok(exists) = fs::exists(path) {
        if !exists {
            return Err(Packman(
                std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    "未能找到项目配置文件 pyproject.toml！",
                ),
                String::from(
                    "尝试从 https://github.com/Koishi007/koishi-ai-pet 下载 pyproject.toml 并重新运行程序",
                ),
            ));
        }
    }
    Ok(())
}

fn install_deps(pb: &ProgressBar) -> Result<()> {
    call_uv(
        DEPS_UV_ARGS,
        pb,
        String::from(
            "尝试使用 uv pip install -e . --default-index https://pypi.tuna.tsinghua.edu.cn/simple -p venv/Scripts/python.exe 手动安装；\n或者使用 venv/Scripts/activate.bat 激活虚拟环境后在项目根目录执行 pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple",
        ),
    )?;

    Ok(())
}

#[cfg(test)]
mod test {
    use std::fs;

    use super::*;

    #[test]
    fn test_check_pyproject_at_existing() {
        let dir = std::env::temp_dir().join(format!("koishi-deps-existing-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("pyproject.toml");
        fs::write(&path, "[project]\nname = \"test\"\n").unwrap();

        assert!(check_pyproject_at(path.to_str().unwrap()).is_ok());

        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn test_check_pyproject_at_missing() {
        let dir = std::env::temp_dir().join(format!("koishi-deps-missing-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("pyproject.toml");

        let err = check_pyproject_at(path.to_str().unwrap()).unwrap_err();
        assert!(matches!(&err, Packman(..)));
        assert_eq!(
            err.help().as_str(),
            "尝试从 https://github.com/Koishi007/koishi-ai-pet 下载 pyproject.toml 并重新运行程序"
        );
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn test_check_pyproject_current_dir() {
        // cargo test 以 crate 根目录为工作目录运行，该目录下应存在 pyproject.toml
        assert!(check_pyproject().is_ok());
    }

    #[test]
    fn test_deps_uv_args_shape() {
        assert_eq!(DEPS_UV_ARGS[0], "pip");
        assert_eq!(DEPS_UV_ARGS[1], "install");
        assert_eq!(DEPS_UV_ARGS[2], "-e");
        assert_eq!(DEPS_UV_ARGS[3], ".");
    }

    #[test]
    fn test_deps_uv_args_use_mirror_and_venv_python() {
        assert!(DEPS_UV_ARGS.contains(&"--default-index"));
        assert!(DEPS_UV_ARGS.contains(&"https://pypi.tuna.tsinghua.edu.cn/simple"));
        assert!(DEPS_UV_ARGS.contains(&"-p"));
        assert!(DEPS_UV_ARGS.contains(&"venv/Scripts/python.exe"));
    }

    // NOTE: 我们只测试最关键部分，其余关于环境操作的测试取决于 subprocess 是否正常运作，而我们已经在 subprocess 里进行了详细的测试
    //       （其实是懒得写 fumofumo (ᗜ ˰ ᗜ) ）
}
