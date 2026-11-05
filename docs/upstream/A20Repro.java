import java.util.regex.Pattern;

/**
 * Astron RPA 缺陷最小复现：SensitiveDataSanitizer 的 QUOTED_VALUE 在长 paramJson 上 StackOverflowError
 *
 * 复刻自 v1.1.7 / main 的
 *   backend/robot-service/src/main/java/com/iflytek/rpa/utils/SensitiveDataSanitizer.java
 * 正则字符串与源码逐字符一致（含 Pattern.CASE_INSENSITIVE）。
 *
 * 跑法：
 *   javac A20Repro.java && java A20Repro
 *   想换成生产 JVM 的栈大小：java -Xss1m A20Repro（默认即 1MB）
 *
 * 预期输出：
 *   - "paramJson":"[{...}]"（String 字段的真实序列化形态）→ 2000 字符起 StackOverflowError
 *   - "paramJson":[{...}]（裸数组，本仓库不是这种）→ 两种正则都 OK
 *   ⇒ 命中的是 QUOTED_VALUE，不是 QUOTED_ARRAY_VALUE
 */
public class A20Repro {

    static final String SENSITIVE_KEY =
            "(?:api[ _-]?key|api[ _-]?secret|hashed[ _-]?key|password|passwd|pwd|os[ _-]?pwd|"
          + "terminal[ _-]?pwd|confirm[ _-]?password|old[ _-]?password|new[ _-]?password|"
          + "access[ _-]?token|refresh[ _-]?token|id[ _-]?token|temp[ _-]?token|token|"
          + "authorization|cookie|cache[ _-]?key|session(?:[ _-]?id)?|jsessionid|"
          + "casdoor[ _-]?session[ _-]?id|client[ _-]?secret|private[ _-]?key|"
          + "verification[ _-]?code|verify[ _-]?code|"
          + "sms[ _-]?code|captcha|credential|secret|"
          + "param[ _-]?json)";

    /** 与 v1.1.7 源码一致：值部分是递归写法 */
    static final Pattern QUOTED_VALUE = Pattern.compile(
            "([\"']" + SENSITIVE_KEY + "[\"']\\s*[:=]\\s*)"
          + "(\"(?:\\\\.|[^\"\\\\])*\"|'(?:\\\\.|[^'\\\\])*')",
            Pattern.CASE_INSENSITIVE);

    /** 与 v1.1.7 源码一致：值部分是非递归写法（这条没问题） */
    static final Pattern QUOTED_ARRAY_VALUE = Pattern.compile(
            "([\"']" + SENSITIVE_KEY + "[\"']\\s*[:=]\\s*\\[)[^\\]]*(\\])",
            Pattern.CASE_INSENSITIVE);

    public static void main(String[] args) {
        System.out.println("JDK      = " + System.getProperty("java.version"));
        System.out.println("线程栈   = 1MB（等价于容器默认，未显式 -Xss）");
        System.out.println();

        int[] lens = {600, 900, 1200, 1500, 1600, 1700, 1800, 2000, 2500, 3000, 6000};

        System.out.println("A) 真实形态  {\"paramJson\":\"[{...}]\"}   ← Executor.paramJson 是 String");
        System.out.printf("   %-7s %-24s %-24s%n", "len", "QUOTED_VALUE", "QUOTED_ARRAY_VALUE");
        boolean overflowed = false;
        for (int n : lens) {
            String json = asStringField(n);
            String a = run(QUOTED_VALUE, json, "$1\"[REDACTED]\"");
            String b = run(QUOTED_ARRAY_VALUE, json, "$1\"[REDACTED]\"$2");
            if (a.contains("StackOverflow")) overflowed = true;
            System.out.printf("   %-7d %-24s %-24s%n", n, a, b);
        }

        System.out.println();
        System.out.println("B) 反例形态  {\"paramJson\":[{...}]}     ← 裸数组，本仓库不是这种");
        System.out.printf("   %-7s %-24s %-24s%n", "len", "QUOTED_VALUE", "QUOTED_ARRAY_VALUE");
        for (int n : lens) {
            String json = asRawArray(n);
            String a = run(QUOTED_VALUE, json, "$1\"[REDACTED]\"");
            String b = run(QUOTED_ARRAY_VALUE, json, "$1\"[REDACTED]\"$2");
            System.out.printf("   %-7d %-24s %-24s%n", n, a, b);
        }

        System.out.println();
        System.out.println("结论：");
        System.out.println("  · 真实形态下 QUOTED_VALUE " + (overflowed ? "爆栈 ✔ 与线上现象一致" : "未复现 ✘"));
        System.out.println("  · QUOTED_ARRAY_VALUE 全程不爆 → 它不是根因");
        System.out.println("  · 修复方向：QUOTED_VALUE 值部分改非递归 X*(?:\\\\.X*)*，");
        System.out.println("    并把 formatResult 的 catch(Exception) 改成 catch(Throwable)。");
    }

    /** {"code":"000000","data":[{"paramJson":"[{\"v\":\"AAAA...\"}]"}]} */
    static String asStringField(int valueLen) {
        StringBuilder inner = new StringBuilder("[{\"v\":\"");
        while (inner.length() < valueLen) inner.append("AAAA");
        inner.append("\"}]");
        String escaped = inner.toString().replace("\\", "\\\\").replace("\"", "\\\"");
        return "{\"code\":\"000000\",\"data\":[{\"paramJson\":\"" + escaped + "\"}]}";
    }

    /** {"code":"000000","data":[{"paramJson":[{"v":"AAAA..."}]}]} */
    static String asRawArray(int valueLen) {
        StringBuilder inner = new StringBuilder("[{\"v\":\"");
        while (inner.length() < valueLen) inner.append("AAAA");
        inner.append("\"}]");
        return "{\"code\":\"000000\",\"data\":[{\"paramJson\":" + inner + "}]}";
    }

    /** 单开 1MB 栈跑，捕获 Throwable 以便继续下一轮 */
    static String run(final Pattern p, final String input, final String repl) {
        final String[] out = new String[1];
        Thread t = new Thread(null, new Runnable() {
            public void run() {
                try {
                    long t0 = System.nanoTime();
                    String r = p.matcher(input).replaceAll(repl);
                    long ms = (System.nanoTime() - t0) / 1_000_000L;
                    out[0] = "OK " + ms + "ms len=" + r.length();
                } catch (Throwable e) {
                    out[0] = "*** " + e.getClass().getSimpleName();
                }
            }
        }, "repro", 1024 * 1024);
        t.start();
        try { t.join(); } catch (InterruptedException e) { return "interrupted"; }
        return out[0];
    }
}
