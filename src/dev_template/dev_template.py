import argparse
import configparser
import importlib.resources as package_resources
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Dict, List

from prompt_toolkit.styles import Style
from pydantic import BaseModel, DirectoryPath, ValidationError, field_validator
from questionary import Choice, checkbox, path, text
from tqdm import tqdm

CONFIG = {}
DEFAULT_PACKAGES = []
DEFAULT_PROJECT_PATH = ""
CREATE_SETUP = False
CREATE_PYPROJECT = False
TEMPLATES_COPIED = False
RESERVED_FILE_NAMES = set()

base_style = {
    "qmark": "#bd93f9 bold",
    "question": "#f8f8f2 bold",
    "answer": "#f8f8f2 bold",
}

project_name_style = Style.from_dict({**base_style, "answer": "#8be9fd bold"})
project_dir_style = Style.from_dict({**base_style, "answer": "#50fa7b bold"})
packages_style = Style.from_dict({**base_style, "answer": "#ff79c6 bold"})
setup_options_style = Style.from_dict(
    {
        **base_style,
        "answer": "#f1fa8c bold",
        "highlighted": "#f1fa8c bold",
        "selected": "#50fa7b bold",
    }
)


class ProjectConfig(BaseModel):
    project_path: DirectoryPath
    project_name: str
    successful_packages: List[str]

    @field_validator("project_name")
    def project_name_valid(cls, project_name: str) -> str:
        if project_name.upper() in RESERVED_FILE_NAMES:
            raise ValueError(
                f"Project name '{project_name}' is reserved... Please choose a different name.\n"
            )
        return project_name


def initialize_globals() -> None:
    global \
        CONFIG, \
        DEFAULT_PACKAGES, \
        DEFAULT_PROJECT_PATH, \
        CREATE_SETUP, \
        CREATE_PYPROJECT, \
        TEMPLATES_COPIED, \
        RESERVED_FILE_NAMES

    CONFIG["config_dir"] = get_config_path()
    CONFIG["config_path"] = os.path.join(CONFIG["config_dir"], "config.ini")

    config_path = CONFIG["config_path"]
    if not os.path.exists(config_path):
        default_config_path = os.path.join(
            os.path.dirname(__file__), "config", "config.ini"
        )
        os.makedirs(CONFIG["config_dir"], exist_ok=True)
        shutil.copy(default_config_path, config_path)

    config = configparser.ConfigParser()
    config.read(config_path)

    DEFAULT_PACKAGES = config.get("DEFAULT", "default_packages", fallback="").split(",")
    DEFAULT_PROJECT_PATH = config.get("DEFAULT", "default_project_path", fallback="")
    CREATE_SETUP = config.getboolean("DEFAULT", "create_setup", fallback=False)
    CREATE_PYPROJECT = config.getboolean("DEFAULT", "create_pyproject", fallback=False)
    TEMPLATES_COPIED = config.getboolean("DEFAULT", "templates_copied", fallback=False)
    RESERVED_FILE_NAMES = set(
        name.strip().upper()
        for name in config.get("DEFAULT", "reserved_file_names", fallback="").split(",")
    )

    if not TEMPLATES_COPIED:
        copy_templates()


def get_config_path() -> str:
    if platform.system() == "Windows":
        config_dir = os.path.join(os.getenv("LOCALAPPDATA"), "dev_template")
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".config", "dev_template")
    return config_dir


def setup_logging(log_id: str, max_log_files: int, debug: bool = False) -> None:
    logs_dir = os.path.join(CONFIG["config_dir"], "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, f"{log_id}.log")

    log_level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        filename=log_file,
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    log_files = sorted(
        (os.path.join(logs_dir, f) for f in os.listdir(logs_dir)),
        key=os.path.getmtime,
    )
    while len(log_files) > max_log_files:
        oldest_log = log_files.pop(0)
        os.remove(oldest_log)
        logging.info(f"Removed old log file: {oldest_log}")

    logging.info("Logging is set up.")


def copy_templates() -> None:
    template_dest_path = Path(CONFIG["config_dir"]) / "templates"

    if not template_dest_path.exists():
        template_dest_path.mkdir(parents=True)

    with package_resources.path("dev_template", "templates") as template_src_path:
        for item in template_src_path.rglob("*"):
            relative_path = item.relative_to(template_src_path)
            dest_path = template_dest_path / relative_path
            if item.is_dir():
                dest_path.mkdir(parents=True, exist_ok=True)
            else:
                shutil.copy2(item, dest_path)

    config_path = CONFIG["config_path"]
    config = configparser.ConfigParser()
    config.read(config_path)
    config.set("DEFAULT", "templates_copied", "1")
    with open(config_path, "w") as configfile:
        config.write(configfile)


def input_prompt(config_mode: bool) -> dict:
    if not config_mode:
        while True:
            project_name = (
                text(
                    "Enter the project name:",
                    validate=lambda input: bool(input.strip())
                    or "Project name cannot be empty.",
                    qmark="📝",
                    style=project_name_style,
                )
                .unsafe_ask()
                .strip()
            )

            try:
                ProjectConfig(
                    project_path=".", project_name=project_name, successful_packages=[]
                )
                break
            except ValidationError as e:
                error_message = f"Project name '{project_name}' is reserved. Please choose a different name."
                print(f"\n{error_message}")
                logging.error(f"Error: {str(e)}")
                input("Press Enter to try again...")
                clear_screen()
    else:
        project_name = "N/A"

    project_dir = path(
        "Enter the project directory:",
        validate=lambda input: os.path.isdir(input)
        or f"The directory '{input}' is not valid.",
        only_directories=True,
        qmark="📁",
        default=DEFAULT_PROJECT_PATH,
        style=project_dir_style,
    ).unsafe_ask()

    default_packages_str = ", ".join([package.strip() for package in DEFAULT_PACKAGES])
    packages = text(
        "Enter packages (comma delimited, can be empty):",
        qmark="📦",
        default=default_packages_str,
        style=packages_style,
    ).unsafe_ask()

    packages = re.sub(r"\s+", ",", packages)
    packages = ", ".join(
        filter(
            None,
            [package.strip() for package in packages.split(",") if package.strip()],
        )
    )

    if config_mode:
        choices = [
            Choice(
                title="Create pyproject.toml?",
                value="create_pyproject",
                checked=CREATE_PYPROJECT,
            ),
            Choice(
                title="Create setup.py?",
                value="create_setup",
                checked=CREATE_SETUP,
            ),
        ]

        setup_options = checkbox(
            "Select options for project setup:",
            choices=choices,
            qmark="⚙️",
            pointer="→",
            style=setup_options_style,
        ).unsafe_ask()
    else:
        setup_options = []
        if CREATE_PYPROJECT:
            setup_options.append("create_pyproject")
        if CREATE_SETUP:
            setup_options.append("create_setup")

    return {
        "project_name": project_name,
        "project_path": project_dir,
        "packages": packages,
        "setup_options": setup_options,
    }


def update_config(config_path: str, details: dict) -> None:
    config = configparser.ConfigParser()
    config.read(config_path)

    config["DEFAULT"]["default_project_path"] = details["project_path"]
    config["DEFAULT"]["default_packages"] = details["packages"]
    config["DEFAULT"]["create_setup"] = (
        "1" if "create_setup" in details["setup_options"] else "0"
    )
    config["DEFAULT"]["create_pyproject"] = (
        "1" if "create_pyproject" in details["setup_options"] else "0"
    )

    with open(config_path, "w") as configfile:
        config.write(configfile)

    logging.info(f"Updated configuration file at '{config_path}'")
    print(f"\nUpdated configuration file at '{config_path}'")


def create_project_structure(config: ProjectConfig) -> None:
    full_project_path = os.path.join(config.project_path, config.project_name)

    logging.info(f"Creating project directory '{full_project_path}'")
    print("Creating project directory...")
    create_project_directory(full_project_path)
    logging.info("Created project directory structure.")
    print("Created project directory structure.\n")

    logging.info(f"Creating subdirectories in '{full_project_path}'")
    print("Creating subdirectories...")
    create_subdirectories(full_project_path, config.project_name)
    logging.info("Created project subdirectories.")
    print("Created project subdirectories.\n")

    create_basic_files(full_project_path, config.project_name)

    create_virtualenv(full_project_path, config.project_name)

    successful_packages = install_packages(
        full_project_path, config.project_name, config.successful_packages
    )

    if successful_packages:
        write_successful_packages_to_files(
            full_project_path,
            config.project_name,
            successful_packages,
        )


def create_project_directory(full_project_path: str) -> None:
    try:
        os.makedirs(full_project_path, exist_ok=True)
    except Exception as e:
        logging.error(f"Could not create project directory: {e}")
        raise ValueError(f"Could not create project directory: {e}")


def create_subdirectories(full_project_path: str, project_name: str) -> None:
    os.makedirs(os.path.join(full_project_path, "src", project_name), exist_ok=True)
    os.makedirs(os.path.join(full_project_path, "tests"), exist_ok=True)


def create_basic_files(full_project_path: str, project_name: str) -> None:
    template_dir = os.path.join(CONFIG["config_dir"], "templates")

    files_to_create = {
        "README.md": "README.md",
        ".gitignore": ".gitignore",
        "requirements.txt": "requirements.txt",
        "src/{project_name}/__init__.py": os.path.join("src", "__init__.py"),
        "src/{project_name}/main.py": os.path.join("src", "main.py"),
        "tests/__init__.py": os.path.join("tests", "__init__.py"),
        "tests/test_main.py": os.path.join("tests", "test_main.py"),
    }

    if CREATE_SETUP:
        files_to_create["setup.py"] = "setup.py"

    if CREATE_PYPROJECT:
        files_to_create["pyproject.toml"] = "pyproject.toml"

    with tqdm(
        total=len(files_to_create),
        desc="Generating core files...",
        ncols=100,
        leave=True,
    ) as progress_bar:
        for dest_template, src_template in files_to_create.items():
            dest_file = os.path.join(
                full_project_path, dest_template.format(project_name=project_name)
            )
            src_file = os.path.join(template_dir, src_template)

            os.makedirs(os.path.dirname(dest_file), exist_ok=True)
            shutil.copyfile(src_file, dest_file)
            progress_bar.update(1)
    logging.info(f'Core files created in "{full_project_path}"')
    print("Generated core files.\n")


def create_virtualenv(full_project_path: str, project_name: str) -> None:
    venv_path = os.path.join(full_project_path, f"{project_name}_venv")
    with tqdm(
        total=1,
        desc="Creating virtual environment...",
        ncols=100,
        leave=True,
    ) as progress_bar:
        subprocess.check_call([sys.executable, "-m", "venv", venv_path])
        progress_bar.update(1)
    logging.info(f'Virtual environment created at "{venv_path}"')
    print("Created virtual environment.\n")


def install_packages(
    full_project_path: str, project_name: str, packages: list
) -> List[str]:
    venv_path = os.path.join(full_project_path, f"{project_name}_venv")
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    packages = list(set(packages))

    if not packages:
        logging.info("No packages to install. Skipping")
        print("No packages to install. Skipping...")
        return []

    successful_packages = []
    failed_packages = []

    with tqdm(
        total=len(packages),
        desc="Installing packages...",
        ncols=100,
        leave=True,
    ) as progress_bar:
        for package in packages:
            try:
                subprocess.check_call(
                    [os.path.join(venv_path, bin_dir, "pip"), "install", package],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                successful_packages.append(package)
                logging.info(f'Successfully installed package "{package}"')
            except subprocess.CalledProcessError:
                failed_packages.append(package)
                logging.error(f'Failed to install package "{package}"')
            progress_bar.update(1)

    if successful_packages:
        installed_packages_str = ", ".join(successful_packages)
        logging.info(f"Successfully installed packages: {installed_packages_str}")
        print(f"Successfully installed packages: {installed_packages_str}")

    if failed_packages:
        failed_packages_str = ", ".join(failed_packages)
        logging.error(f"Failed to install packages: {failed_packages_str}")
        print(f"Failed to install packages: {failed_packages_str}")

    return successful_packages


def get_installed_packages(venv_path: str) -> Dict[str, str]:
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    freeze_output = subprocess.check_output(
        [os.path.join(venv_path, bin_dir, "pip"), "freeze"], text=True
    )
    return dict(line.split("==") for line in freeze_output.splitlines())


def update_requirements_txt(
    file_path: str, successful_packages: List[str], package_versions: Dict[str, str]
) -> None:
    with open(file_path, "a") as f:
        for package in successful_packages:
            if package in package_versions:
                f.write(f"{package}=={package_versions[package]}\n")
                logging.info(
                    f'Updated requirements.txt with package "{package}=={package_versions[package]}"'
                )


def update_pyproject_toml(
    file_path: str, successful_packages: List[str], package_versions: Dict[str, str]
) -> None:
    with open(file_path, "r") as f:
        lines = f.readlines()

    with open(file_path, "w") as f:
        for line in lines:
            f.write(line)
            if line.strip() == "dependencies = [":
                for package in successful_packages:
                    if package in package_versions:
                        f.write(f'    "{package}=={package_versions[package]}"' + ",\n")
                        logging.info(
                            f'Updated pyproject.toml with package "{package}=={package_versions[package]}"'
                        )


def write_successful_packages_to_files(
    full_project_path: str,
    project_name: str,
    successful_packages: List[str],
) -> None:
    venv_path = os.path.join(full_project_path, f"{project_name}_venv")
    package_versions = get_installed_packages(venv_path)

    files_to_update = {
        "requirements.txt": (
            update_requirements_txt,
            [successful_packages, package_versions],
        ),
        "pyproject.toml": (
            update_pyproject_toml,
            [successful_packages, package_versions],
        ),
    }

    file_paths = {
        "requirements.txt": os.path.join(full_project_path, "requirements.txt"),
        "pyproject.toml": os.path.join(full_project_path, "pyproject.toml"),
    }

    print()
    with tqdm(
        total=len(files_to_update),
        desc="Writing successful packages to files...",
        ncols=100,
        leave=True,
    ) as progress_bar:
        for file, (update_function, args) in files_to_update.items():
            update_function(file_paths[file], *args)
            progress_bar.update(1)
    logging.info("Updated files with successful packages.")
    print("Updated files with successful packages.\n")


def parse_arguments():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--config", "-c", action="store_true", help="Setup configuration"
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", help="Enable debug logging"
    )
    return parser.parse_args()


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def logo():
    logo = """
    ++++++++++++++++
    | dev_template |
    ++++++++++++++++
    """
    print(logo)


def logo_config():
    logo = """
    +++++++++++++++++
    | modify_config |
    +++++++++++++++++
    """
    print(logo)


def main():
    initialize_globals()
    args = parse_arguments()
    unique_id = str(uuid.uuid4())
    setup_logging(unique_id, debug=args.debug, max_log_files=7)

    try:
        if args.config:
            clear_screen()
            logo_config()
            details = input_prompt(config_mode=True)
            if details:
                update_config(CONFIG["config_path"], details)
            return

        clear_screen()
        logo()

        answers = input_prompt(config_mode=False)

        project_name = answers["project_name"]
        project_path = answers["project_path"]
        packages = [
            pkg.strip() for pkg in answers["packages"].split(",") if pkg.strip()
        ]

        config = ProjectConfig(
            project_name=project_name,
            project_path=project_path,
            successful_packages=packages,
        )

        if not project_path.endswith("/"):
            project_path += "/"

        full_project_path = os.path.join(project_path, project_name)

        logging.info(f'Setting up project "{project_name}" at "{full_project_path}"')
        logging.info(
            f"Global variables: CONFIG={CONFIG}, DEFAULT_PACKAGES={DEFAULT_PACKAGES}, "
            f"DEFAULT_PROJECT_PATH={DEFAULT_PROJECT_PATH}, CREATE_SETUP={CREATE_SETUP}, "
            f"CREATE_PYPROJECT={CREATE_PYPROJECT}, TEMPLATES_COPIED={TEMPLATES_COPIED}, "
            f"RESERVED_FILE_NAMES={RESERVED_FILE_NAMES}"
        )

        print(f"\nSetting up project '{project_name}' at '{full_project_path}'\n")

        create_project_structure(config)

        logging.info(
            f"Project '{project_name}' created successfully at '{full_project_path}'"
        )

        print(
            f"\nProject '{project_name}' created successfully at '{full_project_path}'."
        )

    except KeyboardInterrupt:
        print("\nOperation cancelled by user. Exiting...")
        logging.info("Operation cancelled by user.")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user. Exiting...")
        logging.info("Operation cancelled by user.")
        sys.exit(0)
