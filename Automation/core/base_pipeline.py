from abc import ABC, abstractmethod


class BasePipeline(ABC):

    def __init__(self, name):

        self.name = name


    @abstractmethod
    def run(self, task):
        """Run a Task and return a PipelineResult dictionary."""
        pass
