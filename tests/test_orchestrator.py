import pytest
from argparse import ArgumentParser, ArgumentTypeError
from auto_archiver.core.orchestrator import ArchivingOrchestrator
from auto_archiver.version import __version__
from auto_archiver.core.config import read_yaml, store_yaml
from auto_archiver.core import Metadata
from auto_archiver.core.consts import SetupError

TEST_ORCHESTRATION = "tests/data/test_orchestration.yaml"
TEST_MODULES = "tests/data/test_modules/"


@pytest.fixture
def test_args():
    return [
        "--config",
        TEST_ORCHESTRATION,
        "--module_paths",
        TEST_MODULES,
        "--example_module.required_field",
        "some_value",
    ]  # just set this for normal testing, we will remove it later


@pytest.fixture
def orchestrator():
    return ArchivingOrchestrator()


@pytest.fixture
def basic_parser(orchestrator) -> ArgumentParser:
    return orchestrator.setup_basic_parser()


def test_setup_orchestrator(orchestrator):
    assert orchestrator is not None


def test_parse_config():
    pass


def test_parse_basic(basic_parser):
    args = basic_parser.parse_args(["--config", TEST_ORCHESTRATION])
    assert args.config_file == TEST_ORCHESTRATION


@pytest.mark.parametrize("mode", ["simple", "full"])
def test_mode(basic_parser, mode):
    args = basic_parser.parse_args(["--mode", mode])
    assert args.mode == mode


def test_mode_invalid(basic_parser, capsys):
    with pytest.raises(SystemExit) as exit_error:
        basic_parser.parse_args(["--mode", "invalid"])
    assert exit_error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_version(basic_parser, capsys):
    with pytest.raises(SystemExit) as exit_error:
        basic_parser.parse_args(["--version"])
    assert exit_error.value.code == 0
    assert capsys.readouterr().out == f"{__version__}\n"


def test_help(orchestrator, basic_parser, capsys):
    args = basic_parser.parse_args(["--help"])
    assert args.help is True

    # test the show_help() on orchestrator
    with pytest.raises(SystemExit) as exit_error:
        orchestrator.show_help(args)

    assert exit_error.value.code == 0

    logs = capsys.readouterr().out
    assert "Usage: auto-archiver [--help] [--version] [--config CONFIG_FILE]" in logs

    # basic config options
    assert "--version" in logs

    # setting modules options
    assert "--feeders" in logs
    assert "--extractors" in logs

    # authentication options
    assert "--authentication" in logs

    # logging options
    assert "--logging.level" in logs

    # individual module configs
    assert "--gsheet_feeder_db.sheet_id" in logs


def test_add_custom_modules_path(orchestrator, test_args):
    orchestrator.setup_config(test_args)

    import auto_archiver

    assert "tests/data/test_modules/" in auto_archiver.modules.__path__


def test_add_custom_modules_path_invalid(orchestrator, caplog, test_args):
    orchestrator.setup_config(
        test_args  # we still need to load the real path to get the example_module
        + ["--module_paths", "tests/data/invalid_test_modules/"]
    )

    assert caplog.records[0].message == "Path 'tests/data/invalid_test_modules/' does not exist. Skipping..."


def test_check_required_values(orchestrator, caplog, test_args):
    # drop the example_module.required_field from the test_args
    test_args = test_args[:-2]

    with pytest.raises(SystemExit):
        orchestrator.setup_config(test_args)
    assert "the following arguments are required: --example_module.required_field" in caplog.records[0].message


def test_get_required_values_from_config(orchestrator, test_args, tmp_path):
    # load the default example yaml, add a required field, then run the orchestrator
    test_yaml = read_yaml(TEST_ORCHESTRATION)
    test_yaml["example_module"] = {"required_field": "some_value"}
    # write it to a temp file
    tmp_file = (tmp_path / "temp_config.yaml").as_posix()
    store_yaml(test_yaml, tmp_file)

    # run the orchestrator
    config = orchestrator.setup_config(["--config", tmp_file, "--module_paths", TEST_MODULES])
    assert config is not None


def test_load_authentication_string(orchestrator, test_args):
    config = orchestrator.setup_config(
        test_args + ["--authentication", '{"facebook.com": {"username": "my_username", "password": "my_password"}}']
    )
    assert config["authentication"] == {"facebook.com": {"username": "my_username", "password": "my_password"}}


def test_load_authentication_string_concat_site(orchestrator, test_args):
    config = orchestrator.setup_config(test_args + ["--authentication", '{"x.com,twitter.com": {"api_key": "my_key"}}'])
    assert config["authentication"] == {"x.com": {"api_key": "my_key"}, "twitter.com": {"api_key": "my_key"}}


def test_load_invalid_authentication_string(orchestrator, test_args):
    with pytest.raises(ArgumentTypeError):
        orchestrator.setup_config(test_args + ["--authentication", "{''invalid_json"])


def test_load_authentication_invalid_dict(orchestrator, test_args):
    with pytest.raises(ArgumentTypeError):
        orchestrator.setup_config(test_args + ["--authentication", "[true, false]"])


def test_load_modules_from_commandline(orchestrator, test_args):
    args = test_args + [
        "--feeders",
        "example_module",
        "--extractors",
        "example_module",
        "--databases",
        "example_module",
        "--enrichers",
        "example_module",
        "--formatters",
        "example_module",
    ]

    orchestrator.setup(args)

    assert len(orchestrator.feeders) == 1
    assert len(orchestrator.extractors) == 1
    assert len(orchestrator.databases) == 1
    assert len(orchestrator.enrichers) == 1
    assert len(orchestrator.formatters) == 1

    assert orchestrator.feeders[0].name == "example_module"
    assert orchestrator.extractors[0].name == "example_module"
    assert orchestrator.databases[0].name == "example_module"
    assert orchestrator.enrichers[0].name == "example_module"
    assert orchestrator.formatters[0].name == "example_module"


def test_load_settings_for_module_from_commandline(orchestrator, test_args):
    args = test_args + [
        "--feeders",
        "gsheet_feeder_db",
        "--gsheet_feeder_db.sheet_id",
        "123",
        "--gsheet_feeder_db.service_account",
        "tests/data/test_service_account.json",
    ]

    orchestrator.setup(args)

    assert len(orchestrator.feeders) == 1
    assert orchestrator.feeders[0].name == "gsheet_feeder_db"
    assert orchestrator.config["gsheet_feeder_db"]["sheet_id"] == "123"


def test_multiple_orchestrator(test_args):
    o1_args = test_args + [
        "--feeders",
        "gsheet_feeder_db",
        "--gsheet_feeder_db.service_account",
        "tests/data/test_service_account.json",
    ]
    o1 = ArchivingOrchestrator()

    with pytest.raises(ValueError):
        # this should fail because the gsheet_feeder_db requires a sheet_id / sheet
        o1.setup(o1_args)

    o2_args = test_args + ["--feeders", "example_module"]
    o2 = ArchivingOrchestrator()
    o2.setup(o2_args)

    assert o2.feeders[0].name == "example_module"

    output: Metadata = list(o2.feed())
    assert len(output) == 1
    assert output[0].get_url() == "https://example.com"


def test_wrong_step_type(test_args, caplog):
    args = test_args + [
        "--feeders",
        "example_extractor",  # example_extractor is not a valid feeder!
    ]

    orchestrator = ArchivingOrchestrator()
    with pytest.raises(SetupError) as err:
        orchestrator.setup(args)
        assert "Module 'example_extractor' is not a feeder" in str(err.value)


def test_load_failed_extractor_cleanup(test_args, mocker, caplog):
    orchestrator = ArchivingOrchestrator()

    # hack to set up the paths so we can patch properly
    orchestrator.module_factory.setup_paths([TEST_MODULES])

    # patch example_module.setup to throw an exception
    mocker.patch(
        "auto_archiver.modules.example_extractor.example_extractor.ExampleExtractor.setup",
        side_effect=Exception("Test exception"),
    )

    with pytest.raises(Exception):
        orchestrator.setup(test_args + ["--extractors", "example_extractor"])

    assert "Error during setup of modules: Test exception" in caplog.text
    # make sure the 'cleanup' is called
    assert "cleanup" in caplog.text
