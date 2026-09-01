use bpaf::{Bpaf, Parser};

pub mod errors;
pub mod install;
pub(crate) mod subprocess;

#[derive(Clone, Bpaf)]
enum Commands {
    /// 安装恋恋桌宠
    #[bpaf(command)]
    Install,
}

pub fn main() {
    let command = commands().run();
    match command {
        Commands::Install => install::process(),
    };
}
