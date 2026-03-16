#include "mock_frame_sensor.hpp"

#include <chrono>
#include <random>
#include <thread>

// Prague center: 50.08°N, 14.42°E
static constexpr double BASE_LAT = 50.08;
static constexpr double BASE_LON = 14.42;
static constexpr double BASE_ALT = 300.0;
static constexpr double GPS_JITTER = 0.001; // ~100m

MockFrameSensor::MockFrameSensor(std::string sensor_type, std::string sensor_name,
                                 uint32_t data_size_bytes, uint32_t interval_ms)
    : sensor_type_(std::move(sensor_type)),
      sensor_name_(std::move(sensor_name)),
      data_size_bytes_(data_size_bytes),
      interval_ms_(interval_ms) {}

FrameData MockFrameSensor::get_latest_frame() {
    // Simulate hardware wait
    std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms_));

    // Generate dummy payload
    std::vector<uint8_t> data(data_size_bytes_, 0xAB);

    // Randomised GPS around Prague
    thread_local std::mt19937 rng(std::random_device{}());
    std::uniform_real_distribution<double> jitter(-GPS_JITTER, GPS_JITTER);

    return FrameData{
        .data = std::move(data),
        .sensor_type = sensor_type_,
        .sensor_name = sensor_name_,
        .latitude = BASE_LAT + jitter(rng),
        .longitude = BASE_LON + jitter(rng),
        .altitude = BASE_ALT + jitter(rng) * 1000.0,
    };
}
