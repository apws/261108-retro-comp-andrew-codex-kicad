// FirstZ80 comparison: extend the existing Codex planner with a Manhattan mode.
// The included planner remains unchanged for the earlier frozen experiments.
#define find_octilinear_route find_base_octilinear_route
#include "octilinear_router.cpp"
#undef find_octilinear_route

extern "C" int find_manhattan_route(
        int w, int ht, int sx, int sy,
        const uint8_t* front, const uint8_t* back, const uint8_t* no_via,
        const uint8_t* goals, int via_cost, int,
        uint32_t* output, int capacity, uint64_t* expanded, int expansion_limit) {
    const uint32_t stride=w*ht, states=2*stride;
    std::vector<uint32_t> heuristic(stride),distance(states,INF),parent(states,INF);
    octile_distances(goals,heuristic.data(),w,ht);
    const uint8_t* blocked[2]={front,back};
    std::priority_queue<Entry> queue;
    for(int layer=0;layer<2;++layer) {
        uint32_t state=layer*stride+sy*w+sx;
        if(!blocked[layer][state%stride]) {
            distance[state]=0;queue.push({heuristic[state%stride],0,state});
        }
    }
    *expanded=0;uint32_t found=INF;
    while(!queue.empty()) {
        Entry item=queue.top();queue.pop();
        if(distance[item.state]!=item.g)continue;
        uint32_t cell=item.state%stride;int layer=item.state/stride,x=cell%w,y=cell/w;
        if(goals[item.state]){found=item.state;break;}
        if(++*expanded>static_cast<uint64_t>(expansion_limit))return -2;
        auto relax=[&](uint32_t next,uint32_t cost) {
            uint32_t g=item.g+cost;
            if(g<distance[next]){distance[next]=g;parent[next]=item.state;queue.push({g+heuristic[next%stride],g,next});}
        };
        for(int dir=0;dir<8;dir+=2) {
            int nx=x+DX[dir],ny=y+DY[dir];
            if(nx<0||nx>=w||ny<0||ny>=ht)continue;
            uint32_t nc=ny*w+nx;if(blocked[layer][nc])continue;
            relax(layer*stride+nc,((layer==0&&DY[dir]==0)||(layer==1&&DX[dir]==0))?100:250);
        }
        if(!no_via[cell]&&!blocked[1-layer][cell])relax((1-layer)*stride+cell,via_cost);
    }
    if(found==INF)return -1;
    std::vector<uint32_t> path;
    for(uint32_t s=found;s!=INF;s=parent[s])path.push_back(s);
    if(path.size()>static_cast<size_t>(capacity))return -3;
    std::reverse(path.begin(),path.end());std::copy(path.begin(),path.end(),output);
    return path.size();
}
