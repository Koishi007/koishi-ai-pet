use std::{
    env,
    error::Error,
    fs::{self, File},
    io::{self, Write},
    path::PathBuf,
    str::FromStr,
};

use owo_colors::OwoColorize;
use zip_extensions::zip_extract::zip_extract;

pub mod check;
pub mod deps;
pub mod venv;

const PREFAB_VENV_ZIP: &[u8] = include_bytes!("../prefab/venv.zip");

macro_rules! error {
    ($string:literal, $err:ident, $help:expr, $file:ident) => {{
        if let Ok(ref mut f) = $file {
            let msg = format!(
                "{} {}\n{} {}\n\n{}{}\n\n{}\n{}\n\n",
                "×",
                $string,
                "╰──>",
                $err,
                "帮助：",
                $help,
                "错误对象：",
                format!("{:#?}", $err)
                    .lines()
                    .map(|l| format!("    {}", l))
                    .collect::<Vec<_>>()
                    .join("\n")
            );
            let _ = &f.write_all(&msg.into_bytes());
        }

        eprintln!(
            "{} {}\n{} {}\n{} {}",
            "×".bright_red(),
            $string.bright_red(),
            "╰──>".bright_red().dimmed(),
            $err.bright_red().dimmed(),
            "帮助：".dimmed(),
            $help.dimmed()
        );
    }};
}

pub fn process() -> bool {
    let _ = File::create("errors.txt");
    let mut file = File::options().append(true).open("errros.txt");

    // 虚拟环境创建
    let result = venv::process();
    if let Err(err) = result {
        error!("虚拟环境创建失败", err, err.help(), file);

        let ans = inquire::Confirm::new(
            &"我们将尝试使用内置的虚拟环境进行修复，是否确认？"
                .bright_blue()
                .to_string(),
        )
        .with_help_message("（y 代表是，n 代表否）此为后备方案，无法确保能够解决问题！")
        .with_error_message("请输入正确的答案！（y 代表是，n 代表否）")
        .prompt();

        if ans.is_ok_and(|a| a) {
            let success = fix_env();
            match success {
                Ok(_) => eprintln!(
                    "{} {}",
                    "✓".bright_green().bold(),
                    "预制虚拟环境创建成功".bright_blue()
                ),
                Err(err) => {
                    eprintln!(
                        "{} {}",
                        "×".bright_green().bold(),
                        "预制虚拟环境创建失败，安装失败".bright_blue()
                    );
                    error!(
                        "预制虚拟环境创建失败",
                        err, "操作系统操作失败，请尝试重试", file
                    );
                    return false;
                }
            }
        }
    }

    // 安装依赖
    let result = deps::process();
    if let Err(err) = result {
        {
            error!("依赖安装失败", err, err.help(), file);
            return false;
        };
    }

    // 检查
    let result = check::process();
    match result {
        Ok(_) => eprintln!("{} {}", "✓".green().bold(), "所有检查通过".bright_blue()),
        Err(err) => {
            error!("检查失败", err, err.help(), file);
            return false;
        }
    }

    let path = match fs::canonicalize("venv/Scripts/koishi.exe") {
        Ok(abs) => {
            let p = abs.display().to_string();
            if p.starts_with(r"\\?\") {
                p.strip_prefix(r"\\?\").unwrap_or(&p).to_string()
            } else {
                p
            }
        }
        Err(_) => "venv/Scripts/koishi.exe".to_string(),
    };

    println!(
        "{} {} {} {}",
        "[!]".bright_green().bold(),
        "安装成功！你可以在".bright_green(),
        path.bright_blue(),
        "找到安装好的桌宠。双击运行即可！".bright_green()
    );

    true
}

fn fix_env() -> std::result::Result<(), Box<dyn Error>> {
    let mut file = File::create("venv.zip")?;

    file.write(PREFAB_VENV_ZIP)?;

    let result = fs::remove_dir_all("venv");
    if let Err(err) = result {
        if err.kind() != io::ErrorKind::NotFound {
            return Err(Box::new(err));
        }
    }

    fs::create_dir("venv")?;

    let root_path = PathBuf::from_str("venv.zip")?;
    let target_dir = PathBuf::from_str("venv")?;
    zip_extract(&root_path, &target_dir)?;

    let home = PathBuf::from_str(&env::var("USERPROFILE")?)?
        .join(".local")
        .join("bin")
        .join("python3.13.exe");
    let content = format!(
        "home = {}\n{}",
        home.display(),
        r#"implementation = CPython
uv = 0.12.5
version_info = 3.13
include-system-site-packages = false
prompt = Rust
"#
    );
    fs::write("venv/pyvenv.cfg", content)?;

    Ok(())
}
