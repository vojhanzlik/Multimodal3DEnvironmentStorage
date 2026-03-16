from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SensorConfig:
    """Configuration for a single sensor attached to the robot."""

    name: str
    type: str
    driver: str
    params: dict[str, Any]
    reductstore_bucket: str


@dataclass
class RobotConfig:
    """Top-level robot configuration loaded from YAML."""

    robot_id: str
    robot_type: str
    sensors: list[SensorConfig]


def load_config(path: Path | str = "robot_config.yaml") -> RobotConfig:
    """Load and validate robot configuration from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError("Config file is empty")

    robot_id = raw.get("robot_id", "")
    if not robot_id:
        raise ValueError("robot_id must be a non-empty string")

    robot_type = raw.get("robot_type", "")
    if not robot_type:
        raise ValueError("robot_type must be a non-empty string")

    raw_sensors = raw.get("sensors", [])
    if not raw_sensors:
        raise ValueError("sensors list must be non-empty")

    sensors = [
        SensorConfig(
            name=s["name"],
            type=s["type"],
            driver=s["driver"],
            params=s.get("params", {}),
            reductstore_bucket=s["reductstore_bucket"],
        )
        for s in raw_sensors
    ]

    return RobotConfig(robot_id=robot_id, robot_type=robot_type, sensors=sensors)
