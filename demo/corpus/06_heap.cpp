// 06_heap.cpp —— 手写二叉堆（优先队列）：任务调度器按优先级取下一个作业。
// 真实场景：调度器不使用 std::priority_queue，而是自己维护一个定长数组堆，
// 这样比较规则（这里只看 prio）和存放位置都能自己控制。
#include <cstdio>
#include <vector>

using u64 = unsigned long long;

struct Job {
    u64 prio;
    u64 id;
};

// 定长数组堆：data 预分配，size 是当前元素个数。
struct JobHeap {
    std::vector<Job> data;
    std::size_t size;
};

static void heap_init(JobHeap& h, std::size_t cap) {
    h.size = 0;
    for (std::size_t i = 0; i < cap; ++i) {
        h.data.push_back(Job{0, 0});
    }
}

// 上浮：新作业放到末尾后，沿父链换到合适位置。
static void sift_up(JobHeap& h, std::size_t i) {
    for (std::size_t step = 0; step < h.data.size(); ++step) {
        if (i == 0) break;
        std::size_t parent = (i - 1) / 2;
        if (h.data[parent].prio >= h.data[i].prio) break;
        Job tmp = h.data[parent];
        h.data[parent] = h.data[i];
        h.data[i] = tmp;
        i = parent;
    }
}

// 下沉：根被取走后，把末尾元素放到根，再沿较大的孩子往下换。
static void sift_down(JobHeap& h, std::size_t i) {
    for (std::size_t step = 0; step < h.data.size(); ++step) {
        std::size_t left = 2 * i + 1;
        std::size_t right = 2 * i + 2;
        std::size_t best = i;
        if (left < h.size && h.data[left].prio > h.data[best].prio) best = left;
        if (right < h.size && h.data[right].prio > h.data[best].prio) best = right;
        if (best == i) break;
        Job tmp = h.data[i];
        h.data[i] = h.data[best];
        h.data[best] = tmp;
        i = best;
    }
}

static void heap_push(JobHeap& h, Job job) {
    h.data[h.size] = job;
    h.size += 1;
    sift_up(h, h.size - 1);
}

static Job heap_pop(JobHeap& h) {
    Job top = h.data[0];
    h.size -= 1;
    h.data[0] = h.data[h.size];
    if (h.size > 0) sift_down(h, 0);
    return top;
}

int main() {
    JobHeap heap;
    heap_init(heap, 8);
    std::vector<Job> jobs;
    jobs.push_back(Job{7, 101});
    jobs.push_back(Job{3, 102});
    jobs.push_back(Job{9, 103});
    jobs.push_back(Job{5, 104});
    jobs.push_back(Job{8, 105});
    jobs.push_back(Job{9, 106});
    for (auto job : jobs) {
        heap_push(heap, job);
    }

    std::printf("heap size: %llu\n", heap.size);
    u64 total = 0;
    for (std::size_t k = 0; k < 3; ++k) {
        Job top = heap_pop(heap);
        std::printf("run job %llu (prio %llu)\n", top.id, top.prio);
        total += top.prio;
    }
    std::printf("remaining: %llu, served prio: %llu\n", heap.size, total);
    return 0;
}

