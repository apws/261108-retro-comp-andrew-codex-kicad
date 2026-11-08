// Codex adaptation helper: exact obstacle-free distance to the AGY net tree.
// This replaces the Python loop over every goal with the same Manhattan metric.
#include <algorithm>
#include <cstdint>

extern "C" void manhattan_distance(const uint8_t* goals, uint16_t* result,
                                    int width, int height) {
    const int count = width * height;
    for (int i = 0; i < count; ++i) result[i] = goals[i] ? 0 : 30000;
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            const int i = y * width + x;
            if (x) result[i] = std::min<int>(result[i], result[i - 1] + 1);
            if (y) result[i] = std::min<int>(result[i], result[i - width] + 1);
        }
    }
    for (int y = height - 1; y >= 0; --y) {
        for (int x = width - 1; x >= 0; --x) {
            const int i = y * width + x;
            if (x + 1 < width) result[i] = std::min<int>(result[i], result[i + 1] + 1);
            if (y + 1 < height) result[i] = std::min<int>(result[i], result[i + width] + 1);
        }
    }
}
