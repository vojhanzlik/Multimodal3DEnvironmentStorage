#pragma once

#include "sensor_interface.hpp"
#include <cstdint>
#include <string>

class MockFrameSensor : public IFrameSensor {
public:
    MockFrameSensor(std::string sensor_type, std::string sensor_name,
                    uint32_t data_size_bytes, uint32_t interval_ms);

    FrameData get_latest_frame() override;

private:
    std::string sensor_type_;
    std::string sensor_name_;
    uint32_t data_size_bytes_;
    uint32_t interval_ms_;
};
