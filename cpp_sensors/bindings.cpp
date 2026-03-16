#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include "mock_frame_sensor.hpp"
#include "mock_video_sensor.hpp"
#include "sensor_factory.hpp"
#include "sensor_interface.hpp"

namespace nb = nanobind;

NB_MODULE(cpp_hal, m) {
    m.doc() = "C++ hardware abstraction layer for robot sensors";

    // --- Data structs ---
    nb::class_<FrameData>(m, "FrameData")
        .def_ro("data", &FrameData::data)
        .def_ro("sensor_type", &FrameData::sensor_type)
        .def_ro("sensor_name", &FrameData::sensor_name)
        .def_ro("latitude", &FrameData::latitude)
        .def_ro("longitude", &FrameData::longitude)
        .def_ro("altitude", &FrameData::altitude);

    nb::class_<VideoChunkData>(m, "VideoChunkData")
        .def_ro("data", &VideoChunkData::data)
        .def_ro("sensor_type", &VideoChunkData::sensor_type)
        .def_ro("sensor_name", &VideoChunkData::sensor_name)
        .def_ro("latitude", &VideoChunkData::latitude)
        .def_ro("longitude", &VideoChunkData::longitude)
        .def_ro("altitude", &VideoChunkData::altitude)
        .def_ro("time_start", &VideoChunkData::time_start)
        .def_ro("time_end", &VideoChunkData::time_end);

    // --- Sensor base classes ---
    nb::class_<IFrameSensor>(m, "IFrameSensor")
        .def("get_latest_frame", [](IFrameSensor& self) {
            nb::gil_scoped_release release;
            return self.get_latest_frame();
        });

    nb::class_<IVideoSensor>(m, "IVideoSensor")
        .def("get_latest_chunk", [](IVideoSensor& self) {
            nb::gil_scoped_release release;
            return self.get_latest_chunk();
        });

    // --- Concrete sensor classes (needed for unique_ptr conversion) ---
    nb::class_<MockFrameSensor, IFrameSensor>(m, "MockFrameSensor");
    nb::class_<MockVideoSensor, IVideoSensor>(m, "MockVideoSensor");

    // --- Factory functions ---
    m.def("create_frame_sensor", &create_frame_sensor,
          nb::arg("name"), nb::arg("type"),
          nb::arg("data_size_bytes"), nb::arg("interval_ms"),
          "Create a frame sensor (discrete capture).");

    m.def("create_video_sensor", &create_video_sensor,
          nb::arg("name"), nb::arg("type"),
          nb::arg("chunk_size_bytes"), nb::arg("chunk_duration_sec"),
          "Create a video sensor (chunk-based capture).");
}
