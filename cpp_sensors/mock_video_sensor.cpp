#include "mock_video_sensor.hpp"

#include <chrono>
#include <random>
#include <thread>

static constexpr double BASE_LAT = 50.08;
static constexpr double BASE_LON = 14.42;
static constexpr double BASE_ALT = 300.0;
static constexpr double GPS_JITTER = 0.001;

MockVideoSensor::MockVideoSensor(std::string sensor_type, std::string sensor_name,
                                  uint32_t chunk_size_bytes, double chunk_duration_sec)
    : sensor_type_(std::move(sensor_type)),
      sensor_name_(std::move(sensor_name)),
      chunk_size_bytes_(chunk_size_bytes),
      chunk_duration_sec_(chunk_duration_sec) {}

VideoChunkData MockVideoSensor::get_latest_chunk() {
    using clock = std::chrono::system_clock;
    auto start = clock::now();

    // Record time_start as UNIX timestamp (seconds)
    double ts_start =
        std::chrono::duration<double>(start.time_since_epoch()).count();

    // Simulate waiting for a full chunk duration
    auto sleep_ms = static_cast<long long>(chunk_duration_sec_ * 1000.0);
    std::this_thread::sleep_for(std::chrono::milliseconds(sleep_ms));

    double ts_end =
        std::chrono::duration<double>(clock::now().time_since_epoch()).count();

    // Generate dummy encoded video payload
    std::vector<uint8_t> data(chunk_size_bytes_, 0xCD);

    thread_local std::mt19937 rng(std::random_device{}());
    std::uniform_real_distribution<double> jitter(-GPS_JITTER, GPS_JITTER);

    return VideoChunkData{
        .data = std::move(data),
        .sensor_type = sensor_type_,
        .sensor_name = sensor_name_,
        .latitude = BASE_LAT + jitter(rng),
        .longitude = BASE_LON + jitter(rng),
        .altitude = BASE_ALT + jitter(rng) * 1000.0,
        .time_start = ts_start,
        .time_end = ts_end,
    };
}
