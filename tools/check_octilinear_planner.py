#!/usr/bin/env python3
"""Small constraint checks for the new planner, independent of board DRC.

Exercises diagonal paths, corner-cut prevention and a wall that requires
two permitted layer transitions. These do not replace native copper checks.
"""
import ctypes
from route_codex import Planner, buffer


def main():
    planner = Planner()
    w, h = 19, 17
    stride = w * h
    goals = [(2, 4), (14, 13), (18, 0)]
    mask = bytearray(2 * stride)
    for x, y in goals:
        mask[y * w + x] = 1
    distances = (ctypes.c_uint32 * stride)()
    planner.lib.octile_distances(buffer(mask), distances, w, h)
    for y in range(h):
        for x in range(w):
            expected = min(100 * max(abs(x - a), abs(y - b)) +
                           41 * min(abs(x - a), abs(y - b)) for a, b in goals)
            assert distances[y * w + x] == expected

    def search(front, back, no_via, start, goal):
        mask = bytearray(2 * stride)
        mask[goal[2] * stride + goal[1] * w + goal[0]] = 1
        output = (ctypes.c_uint32 * 10000)()
        expanded = ctypes.c_uint64()
        n = planner.lib.find_octilinear_route(
            w, h, *start, buffer(front), buffer(back), buffer(no_via),
            buffer(mask), 3000, 180, output, 10000, ctypes.byref(expanded), 100000)
        assert n > 0, n
        path = [(q % stride % w, q % stride // w, q // stride) for q in output[:n]]
        assert path[0][:2] == start and path[-1] == goal
        for a, b in zip(path, path[1:]):
            if a[2] != b[2]:
                assert a[:2] == b[:2] and not no_via[a[1] * w + a[0]]
            else:
                dx, dy = b[0] - a[0], b[1] - a[1]
                assert max(abs(dx), abs(dy)) == 1
                blocked = (front, back)[a[2]]
                assert not blocked[b[1] * w + b[0]]
                if dx and dy:
                    assert not blocked[a[1] * w + b[0]] and not blocked[b[1] * w + a[0]]
        return path

    front, back, no_via = bytearray(stride), bytearray(stride), bytearray(stride)
    path = search(front, back, no_via, (2, 2), (9, 9, 0))
    assert len(path) == 8, 'The unobstructed diagonal should take seven steps'
    for x, y in [(3, 2), (2, 3)]:
        front[y * w + x] = back[y * w + x] = 1
    path = search(front, back, no_via, (2, 2), (9, 9, 0))
    assert path[1][:2] != (3, 3), 'Do not squeeze diagonally through a blocked corner'
    front, back = bytearray(stride), bytearray(stride)
    no_via = bytearray([1]) * stride
    for y in range(h):
        front[y * w + 7] = 1
    back[4 * w + 2] = 1  # Force the route to start on the front.
    for x in (6, 8):
        no_via[4 * w + x] = 0
    path = search(front, back, no_via, (2, 4), (12, 4, 0))
    transitions = [a[:2] for a, b in zip(path, path[1:]) if a[2] != b[2]]
    assert transitions == [(6, 4), (8, 4)]
    print('Planner checks pass: octile heuristic, diagonal route, corner exclusion, controlled via transitions.')


if __name__ == '__main__':
    main()
