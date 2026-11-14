// 05_ring_buffer.cpp —— struct + 成员函数（预期会挡在"没有成员函数"这一条）
#include <cstdio>
#include <vector>

struct Ring {
    std::vector<int> buf;
    std::size_t head;
    std::size_t count;

    Ring(std::size_t cap) : buf(cap, 0), head(0), count(0) {}

    void push(int v) {
        buf[(head + count) % buf.size()] = v;
        if (count == buf.size()) head = (head + 1) % buf.size();
        else count += 1;
    }

    int pop() {
        int v = buf[head];
        head = (head + 1) % buf.size();
        count -= 1;
        return v;
    }
};

int main() {
    Ring r(3);
    r.push(1); r.push(2); r.push(3); r.push(4);
    std::printf("%d %d\n", r.pop(), r.pop());
    return 0;
}
