#include "sensor_factory.hpp"
#include "mock_frame_sensor.hpp"
#include "mock_video_sensor.hpp"

#include <stdexcept>

std::unique_ptr<IFrameSensor> create_frame_sensor(
    const std::string& name, const std::string& type,
    uint32_t data_size_bytes, uint32_t interval_ms) {
    // Currently only MockFrameSensor is available
    return std::make_unique<MockFrameSensor>(type, name, data_size_bytes,
                                             interval_ms);
}

std::unique_ptr<IVideoSensor> create_video_sensor(
    const std::string& name, const std::string& type,
    uint32_t chunk_size_bytes, double chunk_duration_sec) {
    // Currently only MockVideoSensor is available
    return std::make_unique<MockVideoSensor>(type, name, chunk_size_bytes,
                                              chunk_duration_sec);
}
