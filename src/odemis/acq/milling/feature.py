import json
import os
import yaml
from odemis import model
from typing import Dict, List
from odemis.acq.milling.tasks import MillingTaskSettings, FEATURE_ACTIVE
from odemis.util.filename import make_unique_name


class CryoLamellaFeature(object):
    def __init__(self, name: str, position: dict, 
                 milling_tasks: Dict[str, MillingTaskSettings], 
                 status: str,
                 config: dict = None, 
                 alignment: dict = None,
                 focus_position: float = 0.0,): 
        self.name = model.StringVA(name)
        self.position = model.VigilantAttribute(position, unit=["m", "m", "m", "rad", "rad"])
        self.focus_position = model.VigilantAttribute(focus_position, unit="m")                
        self.milling_tasks = milling_tasks
        self.config: dict = config
        self.alignment: dict = alignment
        self.status: str = model.StringVA(status)
        self.reference_image = None
        self.project = None
        self.project_path = None
        self.path = None

    def to_json(self):
        return {
            "name": self.name.value,
            "position": self.position.value,
            "focus_position": self.focus_position.value,
            "milling_tasks": {k: v.to_json() for k, v in self.milling_tasks.items()},
            "config": self.config,
            "alignment": self.alignment,
            "status": self.status.value,
            "project": self.project,
            "project_path": self.project_path,
            "path": self.path,
        }
    
    @classmethod
    def from_json(cls, data: dict):
        feature = cls(
            name=data["name"],
            position=data["position"],
            focus_position=data["focus_position"],
            milling_tasks={k: MillingTaskSettings.from_json(v) for k, v in data["milling_tasks"].items()},
            config=data["config"],
            alignment=data["alignment"],
            status=data["status"]
        )
        feature.project = data["project"]
        feature.project_path = data["project_path"]
        feature.path = data["path"]
        
        return feature

    def __repr__(self):
        return f"CryoLamellaFeature({self.name}, {self.status}, {self.position}, {self.focus_position} {self.milling_tasks}, {self.config}, {self.alignment})"


class CryoLamellaProject:

    def __init__(self, name:str, path: str, features: Dict[str, CryoLamellaFeature] = {}):
        self.name = name
        self.path = path
        self.features = features

    def add_feature(self, feature: CryoLamellaFeature):
        # add project and path metadata
        feature.project = self.name
        feature.project_path = self.path
        feature.path = os.path.join(self.path, feature.name.value)
        
        # create feature directory
        os.makedirs(feature.path, exist_ok=True)
        
        # add feature to project
        self.features[feature.name.value] = feature

    def to_json(self):
        return {
            "name": self.name,
            "path": self.path,
            "features": {k: v.to_json() for k, v in self.features.items()}
        }

    @staticmethod
    def from_json(self, data: dict):
        return CryoLamellaProject(
            name=data["name"],
            path=data["path"],
            features={k: CryoLamellaFeature.from_json(v) for k, v in data["features"].items()}
        )

    def save(self):
        with open(os.path.join(self.path, "project.yaml"), 'w') as f:
            yaml.dump(self.to_json(), f)

    @staticmethod
    def load(self, path: str):
        with open(os.path.join(path, "project.yaml"), 'r') as f:
            data = yaml.safe_load(f)
        
        return CryoLamellaProject.from_json(data)
    
    def __repr__(self) -> str:
        return f"Project: {self.name}, {self.path}, {self.features}"

def create_new_project(path: str, name: str):
    
    # create project directory (dir + name)
    project_dir = os.path.join(path, name)
    os.makedirs(project_dir, exist_ok=True)
    
    # create project
    project = CryoLamellaProject(name=name, path=project_dir)
    
    # save project
    project.save()

    return project


def create_new_feature(name: str, position: Dict[str, float], milling_tasks: Dict[str, MillingTaskSettings], project: CryoLamellaProject, status: str = FEATURE_ACTIVE):
    feature = CryoLamellaFeature(
        make_unique_name(name, project.features),
        position,
        milling_tasks=milling_tasks,
        status=status,
    )
    project.add_feature(feature)
    return feature