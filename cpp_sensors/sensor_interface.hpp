#pragma once

#include <cstdint>
#include <string>
#include <vector>

struct FrameData {
    std::vector<uint8_t> data;
    std::string sensor_type;
    std::string sensor_name;
    double latitude;
    double longitude;
    double altitude;
};

struct VideoChunkData {
    std::vector<uint8_t> data;
    std::string sensor_type;
    std::string sensor_name;
    double latitude;
    double longitude;
    double altitude;
    double time_start;
    double time_end;
};

class IFrameSensor {
public:
    virtual ~IFrameSensor() = default;
    virtual FrameData get_latest_frame() = 0;
};

class IVideoSensor {
public:
    virtual ~IVideoSensor() = default;
    virtual VideoChunkData get_latest_chunk() = 0;
};
