// blur.tu —— 二维取值：**两个标量尺寸**相乘（T-04）
//
// 现有样例的尺寸变量大多来自 `.size()`（`lcs` 是两个向量长度的乘积），
// 但**两个标量形参**当尺寸、且循环嵌套——这条没验证过。
error BlurError;

spec blur(u64 w, u64 h) -> u64 {
    effects {};

    benchmodel {
        data {
            w = 32;
            h = 32;
        }
        scale {
            w in range(1, 64, *2);
            h in range(1, 64, *2);
        }
        seed = 1;
    };

    target { time: { max: O(w * h) }, allocation: { max: 0 }, };
}
