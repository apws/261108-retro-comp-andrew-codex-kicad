// Codex variant 2: direction-aware octilinear A* route planner.
// Native-board integration and obstacle masks are supplied by the Python wrapper.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <queue>
#include <vector>

static constexpr uint32_t INF = std::numeric_limits<uint32_t>::max() / 4;
static constexpr int DX[8] = {1,1,0,-1,-1,-1,0,1};
static constexpr int DY[8] = {0,1,1,1,0,-1,-1,-1};

extern "C" void octile_distances(const uint8_t* goals, uint32_t* h, int w, int ht) {
    const int count = w * ht;
    for (int i = 0; i < count; ++i) h[i] = goals[i] || goals[i+count] ? 0 : INF;
    auto update = [&](int i, int j, int cost) { h[i] = std::min(h[i], h[j] + cost); };
    for (int y = 0; y < ht; ++y) for (int x = 0; x < w; ++x) {
        int i = y*w+x;
        if (x) update(i,i-1,100);
        if (y) {
            update(i,i-w,100);
            if (x) update(i,i-w-1,141);
            if (x+1<w) update(i,i-w+1,141);
        }
    }
    for (int y = ht-1; y >= 0; --y) for (int x = w-1; x >= 0; --x) {
        int i = y*w+x;
        if (x+1<w) update(i,i+1,100);
        if (y+1<ht) {
            update(i,i+w,100);
            if (x) update(i,i+w-1,141);
            if (x+1<w) update(i,i+w+1,141);
        }
    }
}

struct Entry {
    uint32_t f, g, state;
    bool operator<(const Entry& b) const {
        if (f != b.f) return f > b.f;
        if (g != b.g) return g < b.g;
        return state > b.state;
    }
};

// Returns path size, -1 for no path, -2 expansion limit, -3 output too small.
// Path output encodes layer*stride+cell and includes start and finish.
extern "C" int find_octilinear_route(
        int w, int ht, int sx, int sy,
        const uint8_t* front, const uint8_t* back, const uint8_t* no_via,
        const uint8_t* goals, int via_cost, int bend_cost,
        uint32_t* output, int capacity, uint64_t* expanded, int expansion_limit) {
    const uint32_t stride = w * ht, states = 2 * stride * 9;
    std::vector<uint32_t> heuristic(stride), distance(states, INF), parent(states, INF);
    octile_distances(goals, heuristic.data(), w, ht);
    const uint8_t* blocked[2] = {front, back};
    std::priority_queue<Entry> queue;
    const uint32_t start_cell = sy*w+sx;
    for (int layer = 0; layer < 2; ++layer) if (!blocked[layer][start_cell]) {
        uint32_t state = (layer*stride+start_cell)*9+8;
        distance[state] = 0;
        queue.push({heuristic[start_cell],0,state});
    }
    *expanded = 0;
    uint32_t found = INF;
    while (!queue.empty()) {
        const Entry item = queue.top(); queue.pop();
        const uint32_t state = item.state;
        if (distance[state] != item.g) continue;
        const uint32_t lc = state/9, cell = lc%stride;
        const int layer = lc/stride, heading = state%9;
        if (goals[lc]) { found = state; break; }
        if (++*expanded > static_cast<uint64_t>(expansion_limit)) return -2;
        const int x = cell%w, y = cell/w;
        auto relax = [&](uint32_t next, uint32_t cost, uint32_t h) {
            uint32_t g = item.g+cost;
            if (g < distance[next]) {
                distance[next] = g; parent[next] = state;
                queue.push({g+h,g,next});
            }
        };
        for (int dir = 0; dir < 8; ++dir) {
            const int nx = x+DX[dir], ny = y+DY[dir];
            if (nx<0 || nx>=w || ny<0 || ny>=ht) continue;
            const uint32_t next_cell = ny*w+nx;
            if (blocked[layer][next_cell]) continue;
            const bool diagonal = DX[dir] && DY[dir];
            if (diagonal && (blocked[layer][y*w+nx] || blocked[layer][ny*w+x])) continue;
            uint32_t cost = diagonal ? 146 : 100;
            if (!diagonal && ((layer==0 && DX[dir]==0) || (layer==1 && DY[dir]==0))) cost += 25;
            if (heading != 8 && heading != dir) cost += bend_cost;
            relax((layer*stride+next_cell)*9+dir, cost, heuristic[next_cell]);
        }
        if (!no_via[cell] && !blocked[1-layer][cell]) {
            relax(((1-layer)*stride+cell)*9+8, via_cost, heuristic[cell]);
        }
    }
    if (found == INF) return -1;
    std::vector<uint32_t> path;
    for (uint32_t state = found; state != INF; state = parent[state]) path.push_back(state/9);
    if (path.size() > static_cast<size_t>(capacity)) return -3;
    std::reverse(path.begin(),path.end());
    std::copy(path.begin(),path.end(),output);
    return static_cast<int>(path.size());
}
