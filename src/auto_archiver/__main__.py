from . import Config
from . import ArchivingOrchestrator
from loguru import logger

# https://loguru.readthedocs.io/en/stable/overview.html#exceptions-catching-within-threads-or-main
@logger.catch
def main():
    config = Config()
    config.parse()
    orchestrator = ArchivingOrchestrator(config)
    for r in orchestrator.feed(): pass


if __name__ == "__main__":
    main()
