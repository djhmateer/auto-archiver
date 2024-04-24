from . import Config
from . import ArchivingOrchestrator
from loguru import logger

# https://loguru.readthedocs.io/en/stable/overview.html#exceptions-catching-within-threads-or-main
# trying to not have massive API exceptions in all logfiles as they are ERROR class.
# lets see how it is handled in core/orchestrator.py
@logger.catch
def main():
    config = Config()
    config.parse()
    orchestrator = ArchivingOrchestrator(config)
    for r in orchestrator.feed(): pass


if __name__ == "__main__":
    main()
