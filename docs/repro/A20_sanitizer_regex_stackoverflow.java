import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Pattern;

/**
 * A20 复现：SensitiveDataSanitizer 的**第一个**正则 QUOTED_ARRAY_VALUE 在长 paramJson 上抛 StackOverflowError
 *
 * ⚠️ 2026-11-05 14:10 更正：之前这个文件写的是 QUOTED_VALUE。真正爆的是排在它**前面**的
 *    QUOTED_ARRAY_VALUE（`paramJson` 是 JSON **数组**字符串，先被它整段吞掉，
 *    根本轮不到 QUOTED_VALUE）。用 QUOTED_VALUE 测出来的阈值全部作废。
 *    本文件保留历史代码，**阈值以 docs/开发进度.md §26.10 的 inner 表为准**。
 *
 * 【现象】
 *   正式服 183 上点「定时任务 → 添加应用」，弹窗报「系统异常，请稍后重试」+「暂无数据」。
 *   接口 GET /api/robot/triggerTask/robotExe/list 返回 HTTP 200 但响应体只有 83 字节
 *   （body 内 code=999999）。把该机器人的参数降下来后立刻恢复。
 *
 * 【根因】
 *   1. ApiLogAspect 记「API 成功」日志时执行：
 *          String s = JSON.toJSONString(result);          // fastjson
 *          s = SensitiveDataSanitizer.sanitize(s);        // 7 个正则依次脱敏
 *          if (s.length() > 2000) s = s.substring(0, 2000) + "...[已截取]";   // 截断在 sanitize **之后**
 *   2. sanitize 里第一个是 QUOTED_ARRAY_VALUE，值部分 `\[(?:\\.|[^\]\\])*\]`
 *      由 java.util.regex.Pattern$Loop **递归**实现，递归深度 ≈ 待匹配字符数。
 *   3. getExecutorList 把每个机器人的全部流程参数序列化进 Executor.paramJson。
 *   4. 递归吃掉 1MB 线程栈 → StackOverflowError 从日志切面抛出 → 全局兜底 → 999999。
 *
 * 【为什么只有某些账号/某些机器人炸】
 *   - 该接口取机器人的 SQL 带 `creator_id = 当前登录用户`，别的账号看不到这条长数据；
 *   - HTTP 是 200，所以 `docker logs | grep ERROR` 也看不到（响应体才 83 字节）。
 *
 * 【临界值（2026-11-05 二次校准，判据 = paramJson 序列化长度 inner）】
 *   1355 OK ｜ **1673 必爆**（NCIC订单处理-华为 的真实 8 条参数，3/3 复现）｜ 2153 爆。
 *   与内容无关，只看长度；含大量 `\"` / `\\` 的真实数据更容易先爆（转义会再放大 1.2 倍）。
 *
 * 【跑法】
 *   javac --release 8 -encoding UTF-8 A20_sanitizer_regex_stackoverflow.java
 *   java -cp . A20_sanitizer_regex_stackoverflow QUOTED_VALUE.regex
 *
 * 【更贴近生产的跑法】直接调 jar 里那个类（推荐，见 §26.10）：
 *   把 com.iflytek.rpa.utils.SensitiveDataSanitizer 抽出来，用反射调 sanitize(Object)，
 *   喂「{\"paramJson\":\"...\"}」形状的日志串，在 .183 容器内用生产 JVM（OpenJDK 1.8.0_504）跑。
 *
 * 【预期】
 *   前几行 OK、后几行 *** StackOverflowError。把正则换成修复版
 *   （值部分改成不递归的 `[^\]\\]*(?:\\.[^\]\\]*)*`）后，全部应为 OK。
 */
public class A20_sanitizer_regex_stackoverflow {

    public static void main(String[] args) throws Exception {
        String rxPath = args.length > 0 ? args[0] : "QUOTED_VALUE.regex";
        String rx = Files.readString(Path.of(rxPath), StandardCharsets.UTF_8).trim();
        Pattern p = Pattern.compile(rx, Pattern.CASE_INSENSITIVE);   // 与 jar 内一致（iconst_2）

        System.out.println("JDK = " + System.getProperty("java.version")
                + "，正则长度 = " + rx.length() + "，编译标志 = CASE_INSENSITIVE");
        System.out.println("（线程栈用 JVM 默认值；平台容器未显式设置 -Xss）\n");

        System.out.println("对照组：paramJson 的值用 'A' 填充，只测长度，证明与内容无关");
        for (int n : new int[]{200, 800, 1200, 1600, 1800, 2000, 2400}) {
            probe(p, n);
        }

        System.out.println("\n真实场景：NCIC订单处理-华为 · 8 条参数 → inner 1673 / 日志串 2112（实测 3/3 爆栈）");
        probe(p, 1673);
    }

    private static void probe(Pattern p, int n) {
        String json = "{\"paramJson\":\"" + "A".repeat(n) + "\"}";
        long t0 = System.currentTimeMillis();
        String tag;
        try {
            p.matcher(json).replaceAll("[X]");
            tag = "OK";
        } catch (StackOverflowError e) {
            tag = "*** StackOverflowError";
        }
        System.out.printf("  valueLen=%5d  jsonLen=%5d  %-22s %4d ms%n",
                n, json.length(), tag, System.currentTimeMillis() - t0);
    }
}
