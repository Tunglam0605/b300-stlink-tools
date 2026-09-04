#include <cstdint>
#include <string>
#include <vector>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "b300/trace_decoder.hpp"

namespace py = pybind11;

namespace {

py::list decode_fixed_width(
    py::bytes payload,
    std::uint16_t channel,
    std::uint64_t timestamp_ns,
    std::uint32_t source_id
) {
    const std::string bytes = payload;
    b300::FixedWidthValueDecoder decoder(channel);
    std::vector<b300::TraceEvent> events;
    const auto consumed = decoder.decode(
        reinterpret_cast<const std::uint8_t*>(bytes.data()),
        bytes.size(),
        {timestamp_ns, source_id},
        events
    );

    py::list out;
    for (const auto& event : events) {
        py::dict item;
        item["timestamp_ns"] = event.timestamp_ns;
        item["source_id"] = event.source_id;
        item["channel"] = event.channel;
        item["type"] = static_cast<std::uint8_t>(event.type);
        item["value"] = event.value;
        out.append(std::move(item));
    }

    py::dict result;
    result["consumed"] = consumed;
    result["events"] = std::move(out);
    return py::list(py::make_tuple(std::move(result)));
}

} // namespace

PYBIND11_MODULE(_b300_debug_core, module) {
    module.doc() = "B300 native debug data-plane bridge";
    module.attr("ABI_VERSION") = 1;
    module.def(
        "decode_fixed_width",
        &decode_fixed_width,
        py::arg("payload"),
        py::arg("channel"),
        py::arg("timestamp_ns"),
        py::arg("source_id")
    );
}
