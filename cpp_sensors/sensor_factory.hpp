#pragma once

#include "sensor_interface.hpp"
#include <cstdint>
#include <memory>
#include <string>

std::unique_ptr<IFrameSensor> create_frame_sensor(
    const std::string& name, const std::string& type,
    uint32_t data_size_bytes, uint32_t interval_ms);

std::unique_ptr<IVideoSensor> create_video_sensor(
    const std::string& name, const std::string& type,
    uint32_t chunk_size_bytes, double chunk_duration_sec);
