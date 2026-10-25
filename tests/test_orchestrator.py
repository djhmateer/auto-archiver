import os
import pytest
from argparse import ArgumentParser, ArgumentTypeError
from requests.exceptions import SSLError
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


def test_check_for_updates_ssl_error(orchestrator, mocker):
    """check_for_updates should not raise when the HTTP request fails."""
    mocker.patch(
        "auto_archiver.core.orchestrator.requests.get",
        side_effect=SSLError("SSL handshake failed"),
    )
    # should not raise
    orchestrator.check_for_updates()


def test_check_for_updates_timeout(orchestrator, mocker):
    """check_for_updates should not raise on connection timeout."""
    from requests.exceptions import ConnectionError

    mocker.patch(
        "auto_archiver.core.orchestrator.requests.get",
        side_effect=ConnectionError("Connection refused"),
    )
    orchestrator.check_for_updates()


def test_check_for_updates_new_version_available(orchestrator, mocker):
    """check_for_updates should not raise when a newer version exists."""
    mocker.patch(
        "auto_archiver.core.orchestrator.requests.get",
        return_value=mocker.Mock(json=lambda: {"info": {"version": "99.0.0"}}),
    )
    # should complete without error
    orchestrator.check_for_updates()


def _feed_one_slow_row(mocker, tmp_path, row_seconds, orchestrator=None):
    from auto_archiver.core import orchestrator as orch
    import time

    mocker.patch.object(orch, "HANG_DUMP_SECONDS", 0.2)
    mocker.patch.object(orch, "SLOW_ROW_SECONDS", 0.1)
    o = orchestrator or ArchivingOrchestrator()
    o.feeders = [[Metadata().set_url("https://example.com")]]
    o.hang_log = open(tmp_path / "hangs.log", "a")
    mocker.patch.object(o, "feed_item", side_effect=lambda item: time.sleep(row_seconds) or item)
    mocker.patch.object(o, "cleanup")

    output = list(o.feed())
    time.sleep(0.3)  # a watchdog that wasn't cancelled would have fired by now
    o.hang_log.close()
    return output, (tmp_path / "hangs.log").read_text()


def test_hung_row_dumps_thread_stacks(mocker, tmp_path):
    output, hangs = _feed_one_slow_row(mocker, tmp_path, row_seconds=0.5)

    assert output[0].get_url() == "https://example.com"
    assert "row running for" in hangs and "https://example.com" in hangs
    assert "(most recent call first)" in hangs  # faulthandler's stack dump follows the header


def test_quick_row_dumps_nothing(mocker, tmp_path):
    _, hangs = _feed_one_slow_row(mocker, tmp_path, row_seconds=0)

    assert hangs == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://vk.ru/id123",
        "https://m.vk.com/wall-1_2",
        "https://vkvideo.ru/video-1_2",
        "https://www.vk.com/id123",
        "https://VK.ru/id123",
        "https://vk.ru:443/id123",
    ],
)
def test_ip_blocked_site_that_still_times_out(mocker, url):
    connect = mocker.patch("socket.create_connection", side_effect=TimeoutError)
    assert ArchivingOrchestrator._is_ip_blocked(url) is True
    host = connect.call_args[0][0][0]
    assert host in ("vk.ru", "m.vk.com", "vkvideo.ru", "vk.com")


def test_ip_blocked_site_reachable_again(mocker):
    mocker.patch("socket.create_connection")
    warning = mocker.patch("auto_archiver.utils.custom_logger.logger.warning")
    assert ArchivingOrchestrator._is_ip_blocked("https://vk.ru/id123") is False
    warning.assert_called_once()


@pytest.mark.parametrize("url", ["https://example.com/page", "https://notvk.ru/page"])
def test_other_sites_never_checked(mocker, url):
    connect = mocker.patch("socket.create_connection")
    assert ArchivingOrchestrator._is_ip_blocked(url) is False
    connect.assert_not_called()


def test_row_still_running_after_kill_limit_is_marked_failed_and_killed(mocker, tmp_path):
    from auto_archiver.core import orchestrator as orch

    mocker.patch.object(orch, "ROW_KILL_SECONDS", 0.3)
    mocker.patch.object(orch, "_descendant_pids", return_value=[111, 222])
    kill = mocker.patch("os.kill")
    exit_ = mocker.patch("os._exit")
    tmp_dir = tmp_path / "row_tmp"
    tmp_dir.mkdir()
    module = mocker.Mock(tmp_dir=str(tmp_dir))
    mocker.patch.object(ArchivingOrchestrator, "all_modules", new_callable=mocker.PropertyMock, return_value=[module])
    db = mocker.Mock()
    o = ArchivingOrchestrator()
    o.databases = [db]

    _feed_one_slow_row(mocker, tmp_path, row_seconds=0.5, orchestrator=o)

    db.failed.assert_called_once()
    assert "killed" in db.failed.call_args[0][1]
    assert [c.args[0] for c in kill.call_args_list] == [111, 222]
    exit_.assert_called_once_with(1)
    assert not tmp_dir.exists()


def test_kill_timer_firing_after_the_row_finished_does_nothing(mocker):
    exit_ = mocker.patch("os._exit")
    db = mocker.Mock()
    o = ArchivingOrchestrator()
    o.databases = [db]
    o.running_item = Metadata().set_url("https://example.com/next-row")

    o._kill_hung_row(Metadata().set_url("https://example.com/finished-row"))

    db.failed.assert_not_called()
    exit_.assert_not_called()


def test_stuck_sheet_does_not_stop_the_kill(mocker):
    import threading
    from auto_archiver.core import orchestrator as orch

    mocker.patch.object(orch, "KILL_REPORT_SECONDS", 0.1)
    mocker.patch.object(orch, "_descendant_pids", return_value=[])
    mocker.patch.object(ArchivingOrchestrator, "all_modules", new_callable=mocker.PropertyMock, return_value=[])
    exit_ = mocker.patch("os._exit")
    never = threading.Event()
    db = mocker.Mock()
    db.failed.side_effect = lambda *args: never.wait(2)
    o = ArchivingOrchestrator()
    o.databases = [db]
    o.running_item = item = Metadata().set_url("https://example.com")

    o._kill_hung_row(item)

    exit_.assert_called_once_with(1)
    never.set()


def _archive_with_named_modules(mocker, url):
    """runs archive() with mock extractors/enrichers named like the real ones, returning the result and the names run"""
    ran = []

    def module(name):
        m = mocker.Mock()
        m.name = name
        m.sanitize_url.side_effect = lambda u: u
        m.download.side_effect = lambda item: ran.append(name) or False
        m.enrich.side_effect = lambda item: ran.append(name)
        return m

    o = ArchivingOrchestrator()
    o.config = {}
    o.disconnect_vpn = mocker.Mock()
    o.extractors = [module("generic_extractor"), module("wayback_extractor_enricher")]
    o.enrichers = [module("screenshot_enricher"), module("hash_enricher")]
    o.databases, o.storages = [], []
    o.formatters = [mocker.Mock(format=mocker.Mock(return_value=None))]
    return o.archive(Metadata().set_url(url)), ran


def test_ip_blocked_site_only_runs_modules_that_dont_fetch_it(mocker):
    mocker.patch("socket.create_connection", side_effect=TimeoutError)
    result, ran = _archive_with_named_modules(mocker, "https://vk.ru/id123")
    assert ran == ["wayback_extractor_enricher", "hash_enricher"]
    assert "aa IP blocked" in result.get_status_notes()


def test_reachable_site_runs_all_modules(mocker):
    connect = mocker.patch("socket.create_connection")
    result, ran = _archive_with_named_modules(mocker, "https://example.com/page")
    assert ran == ["generic_extractor", "wayback_extractor_enricher", "screenshot_enricher", "hash_enricher"]
    assert result.get_status_notes() == []
    connect.assert_not_called()


def test_kill_still_exits_when_a_process_cant_be_killed(mocker):
    from auto_archiver.core import orchestrator as orch

    mocker.patch.object(orch, "_descendant_pids", return_value=[111, 222])
    kill = mocker.patch("os.kill", side_effect=PermissionError)
    exit_ = mocker.patch("os._exit")
    mocker.patch.object(ArchivingOrchestrator, "all_modules", new_callable=mocker.PropertyMock, return_value=[])
    o = ArchivingOrchestrator()
    o.databases = []
    o.running_item = item = Metadata().set_url("https://example.com")

    o._kill_hung_row(item)

    assert kill.call_count == 2
    exit_.assert_called_once_with(1)


def test_quick_row_is_not_killed(mocker, tmp_path):
    exit_ = mocker.patch("os._exit")
    _feed_one_slow_row(mocker, tmp_path, row_seconds=0)
    exit_.assert_not_called()


def test_descendant_pids_finds_grandchildren():
    import subprocess
    from auto_archiver.core.orchestrator import _descendant_pids

    # a shell (child) that starts sleep (grandchild), like geckodriver starting Firefox
    import time

    shell = subprocess.Popen(["sh", "-c", "sleep 5 & wait"])
    time.sleep(0.2)
    pids = _descendant_pids(shell.pid)
    shell.kill()
    shell.wait()
    for pid in pids:
        os.kill(pid, 9)

    assert len(pids) == 1  # the sleep
