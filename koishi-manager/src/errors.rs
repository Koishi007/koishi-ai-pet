use thiserror::Error;

pub type Result<T> = std::result::Result<T, Errors>;

#[derive(Debug, Error)]
pub enum Errors {
    #[error("解压内置包管理器时发生错误")]
    ExtractPackman(zip::result::ZipError, String),

    #[error("输入输出（I/O）操作发生错误：{0}")]
    Io(std::io::Error, String),

    #[error("文件操作发生错误：{0}")]
    File(std::io::Error, String),

    #[error("包管理器运行发生错误：{0}")]
    Packman(std::io::Error, String),

    #[error("包管理器检查失败")]
    Check(Vec<(crate::install::check::Failure, String)>, String),
}

impl Errors {
    pub fn help(&self) -> &String {
        match self {
            Errors::ExtractPackman(_, help) => help,
            Errors::Io(_, help) => help,
            Errors::File(_, help) => help,
            Errors::Packman(_, help) => help,
            Errors::Check(_, help) => help,
        }
    }
}

pub trait ToIoErrors<T> {
    fn unwrap_io(self, help: String) -> std::result::Result<T, Errors>;
    fn unwrap_file(self, help: String) -> std::result::Result<T, Errors>;
    fn unwrap_packman(self, help: String) -> std::result::Result<T, Errors>;
}

impl<T> ToIoErrors<T> for std::result::Result<T, std::io::Error> {
    fn unwrap_io(self, help: String) -> std::result::Result<T, Errors> {
        match self {
            Ok(value) => Ok(value),
            Err(err) => Err(Errors::Io(err, help)),
        }
    }

    fn unwrap_file(self, help: String) -> std::result::Result<T, Errors> {
        match self {
            Ok(value) => Ok(value),
            Err(err) => Err(Errors::File(err, help)),
        }
    }

    fn unwrap_packman(self, help: String) -> std::result::Result<T, Errors> {
        match self {
            Ok(value) => Ok(value),
            Err(err) => Err(Errors::Packman(err, help)),
        }
    }
}
pub trait ToExtractPackmanErrors<T> {
    fn unwrap_extract_packman(self, help: String) -> std::result::Result<T, Errors>;
}

impl ToExtractPackmanErrors<()> for zip::result::ZipResult<()> {
    fn unwrap_extract_packman(self, help: String) -> std::result::Result<(), Errors> {
        match self {
            Ok(value) => Ok(value),
            Err(err) => Err(Errors::ExtractPackman(err, help)),
        }
    }
}

#[cfg(test)]
mod test {
    use std::io;

    use zip::result::ZipError;

    use super::Errors::*;
    use super::*;

    fn generate_zip_error() -> ZipError {
        ZipError::FileNotFound
    }

    fn generate_io_error() -> io::Error {
        io::Error::from(io::ErrorKind::NotFound)
    }

    fn help_string() -> String {
        String::from("This is a help!")
    }

    #[test]
    fn test_extract_packman_displaying() {
        let err = ExtractPackman(generate_zip_error(), help_string());
        assert_eq!(format!("{}", err), "解压内置包管理器时发生错误");
    }

    #[test]
    fn test_io_displaying() {
        let err = Io(generate_io_error(), help_string());
        assert_eq!(
            format!("{}", err),
            format!("{}{}", "输入输出（I/O）操作发生错误：", generate_io_error())
        );
    }

    #[test]
    fn test_file_displaying() {
        let err = File(generate_io_error(), help_string());
        assert_eq!(
            format!("{}", err),
            format!("{}{}", "文件操作发生错误：", generate_io_error())
        );
    }

    #[test]
    fn test_packman_displaying() {
        let err = Packman(generate_io_error(), help_string());
        assert_eq!(
            format!("{}", err),
            format!("{}{}", "包管理器运行发生错误：", generate_io_error())
        );
    }

    #[test]
    fn test_check_displaying() {
        let err = Check(vec![], help_string());
        assert_eq!(format!("{}", err), "包管理器检查失败");
    }

    #[test]
    fn test_extract_packman_help() {
        let err = ExtractPackman(generate_zip_error(), help_string());
        assert_eq!(err.help(), &help_string());
    }

    #[test]
    fn test_io_help() {
        let err = Io(generate_io_error(), help_string());
        assert_eq!(err.help(), &help_string());
    }

    #[test]
    fn test_file_help() {
        let err = File(generate_io_error(), help_string());
        assert_eq!(err.help(), &help_string());
    }

    #[test]
    fn test_packman_help() {
        let err = Packman(generate_io_error(), help_string());
        assert_eq!(err.help(), &help_string());
    }

    #[test]
    fn test_check_help() {
        let err = Check(vec![], help_string());
        assert_eq!(err.help(), &help_string());
    }

    #[test]
    fn test_unwrap_extract_packman_ok() {
        let rst = zip::result::ZipResult::Ok(());
        assert_eq!(rst.unwrap_extract_packman(help_string()).unwrap(), ());
    }

    #[test]
    fn test_unwrap_extract_packman_err() {
        let rst = zip::result::ZipResult::Err(generate_zip_error());
        let err = rst.unwrap_extract_packman(help_string()).unwrap_err();
        assert!(matches!(err, Errors::ExtractPackman(..)));
    }

    #[test]
    fn test_unwrap_io_ok() {
        let rst: std::result::Result<(), std::io::Error> = Ok(());
        assert_eq!(rst.unwrap_io(help_string()).unwrap(), ());
    }

    #[test]
    fn test_unwrap_io_err() {
        let rst: std::result::Result<(), std::io::Error> = Err(generate_io_error());
        let err = rst.unwrap_io(help_string()).unwrap_err();
        assert!(matches!(err, Errors::Io(..)));
    }

    #[test]
    fn test_unwrap_file_ok() {
        let rst: std::result::Result<(), std::io::Error> = Ok(());
        assert_eq!(rst.unwrap_file(help_string()).unwrap(), ());
    }

    #[test]
    fn test_unwrap_file_err() {
        let rst: std::result::Result<(), std::io::Error> = Err(generate_io_error());
        let err = rst.unwrap_file(help_string()).unwrap_err();
        assert!(matches!(err, Errors::File(..)));
    }

    #[test]
    fn test_unwrap_packman_ok() {
        let rst: std::result::Result<(), std::io::Error> = Ok(());
        assert_eq!(rst.unwrap_packman(help_string()).unwrap(), ());
    }

    #[test]
    fn test_unwrap_packman_err() {
        let rst: std::result::Result<(), std::io::Error> = Err(generate_io_error());
        let err = rst.unwrap_packman(help_string()).unwrap_err();
        assert!(matches!(err, Errors::Packman(..)));
    }
}
