from __future__ import annotations
from dataclasses import dataclass
from abc import abstractmethod, ABC
from metadata import Metadata
from steps.step import Step

@dataclass
class Database(Step, ABC):
    name = "database"

    def __init__(self, config: dict) -> None:
        # without this STEP.__init__ is not called
        super().__init__(config)
        

    # only for typing...
    def init(name: str, config: dict) -> Database:
        return Step.init(name, config, Database)

    @abstractmethod
    def enrich(self, item: Metadata) -> Metadata: pass
