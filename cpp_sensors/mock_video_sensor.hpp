#pragma once

#include "sensor_interface.hpp"
#include <cstdint>
#include <string>

class MockVideoSensor : public IVideoSensor {
public:
    MockVideoSensor(std::string sensor_type, std::string sensor_name,
                    uint32_t chunk_size_bytes, double chunk_duration_sec);

    VideoChunkData get_latest_chunk() override;

private:
    std::string sensor_type_;
    std::string sensor_name_;
    uint32_t chunk_size_bytes_;
    double chunk_duration_sec_;
};
