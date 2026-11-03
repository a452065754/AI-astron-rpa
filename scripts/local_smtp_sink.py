#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地 SMTP 收信器：给「邮件」类原子提供可验证的收信端。

## 为什么需要

`Email.send_email` 是**无出参**原子——发完之后引擎什么都不返回，
「运行成功」根本不能证明邮件真的发出去了（发件箱配错也能"成功"）。
要验证只有一条路：**在收信侧把邮件整封接住**，再核对主题/正文/附件。

本项目此前把 Email 域判为「需外部账号 → 阻塞」。实际上原子的
`mail_server` 支持 `other` + 自定义 `other_mail_server` / `mail_port` / `use_ssl`，
所以指到本地起一个 SMTP 收信器就能完成真验证，不依赖任何外部邮箱。

## 用法

    python scripts/local_smtp_sink.py --port 2525 --out demo/_mail_inbox
    # 另开一个终端跑流程；收到的邮件会写成 *.eml + 同名 *.json 摘要

只读设计：不回复、不转发、不回执，只落盘。仅用于测试/取证。

## 实现说明

不依赖 aiosmtpd / smtpd（后者 Python 3.12 起已从标准库移除），
直接用 socket 实现 smtplib 客户端需要的最小状态机。
AUTH LOGIN / PLAIN 一律接受（收信器不校验密码，目的只是让 login() 返回成功）。
"""
import argparse
import base64
import json
import os
import re
import socket
import threading
import time
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime


def _b(s):
    return s.encode("utf-8", "replace")


class SmtpSink:
    def __init__(self, host, port, out_dir):
        self.host = host
        self.port = port
        self.out_dir = out_dir
        self.count = 0
        self.lock = threading.Lock()
        os.makedirs(out_dir, exist_ok=True)

    # ---------- 落盘 ----------
    def _save(self, raw: bytes, mail_from: str, rcpt: list):
        with self.lock:
            self.count += 1
            n = self.count
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = os.path.join(self.out_dir, "%s_%02d" % (stamp, n))

        with open(base + ".eml", "wb") as f:
            f.write(raw)

        summary = {"mail_from": mail_from, "rcpt_to": list(rcpt), "size": len(raw)}
        try:
            msg = message_from_bytes(raw, policy=policy.default)
            summary["subject"] = str(msg.get("Subject", ""))
            summary["from"] = str(msg.get("From", ""))
            summary["to"] = str(msg.get("To", ""))
            summary["cc"] = str(msg.get("Cc", ""))
            summary["date"] = str(msg.get("Date", ""))
            body_parts, attachments = [], []
            for part in msg.walk():
                if part.is_multipart():
                    continue
                ctype = part.get_content_type()
                fname = part.get_filename()
                if fname:
                    attachments.append(fname)
                elif ctype.startswith("text/"):
                    try:
                        body_parts.append(part.get_content())
                    except Exception:
                        payload = part.get_payload(decode=True) or b""
                        body_parts.append(payload.decode("utf-8", "replace"))
            summary["attachments"] = attachments
            summary["body"] = "\n".join(body_parts)
            try:
                summary["date_parsed"] = parsedate_to_datetime(summary["date"]).isoformat()
            except Exception:
                pass
        except Exception as e:  # 解析失败也要留下证据
            summary["parse_error"] = "%s: %s" % (type(e).__name__, e)

        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("[收信] %s -> %s.eml  主题=%r 附件=%s"
              % (summary.get("from", mail_from), base, summary.get("subject"), summary.get("attachments")),
              flush=True)
        return base

    # ---------- 协议 ----------
    @staticmethod
    def _recv_line(conn, buf):
        while b"\r\n" not in buf[0]:
            chunk = conn.recv(8192)
            if not chunk:
                return None, buf
            buf[0] += chunk
        line, _, rest = buf[0].partition(b"\r\n")
        buf[0] = rest
        return line, buf

    def _handle(self, conn, addr):
        buf = [b""]
        mail_from, rcpt = "", []

        def send(s):
            conn.sendall(_b(s + "\r\n"))

        try:
            conn.settimeout(30)
            send("220 local-rpa-sink ESMTP ready")
            while True:
                line, buf = self._recv_line(conn, buf)
                if line is None:
                    break
                text = line.decode("utf-8", "replace")
                upper = text.upper()

                if upper.startswith("EHLO"):
                    # 多行应答，末行 250 空格
                    conn.sendall(_b("250-local-rpa-sink\r\n250-AUTH LOGIN PLAIN\r\n250-8BITMIME\r\n250 OK\r\n"))
                elif upper.startswith("HELO"):
                    send("250 local-rpa-sink")
                elif upper.startswith("AUTH"):
                    parts = text.split()
                    mech = parts[1].upper() if len(parts) > 1 else ""
                    if len(parts) == 2:  # 需要继续挑战
                        if mech == "LOGIN":
                            send("334 VXNlcm5hbWU6")
                            self._recv_line(conn, buf)
                            send("334 UGFzc3dvcmQ6")
                            self._recv_line(conn, buf)
                        else:  # PLAIN 无初值
                            send("334 ")
                            self._recv_line(conn, buf)
                    send("235 Authentication successful")
                elif upper.startswith("MAIL FROM"):
                    m = re.search(r"<(.*?)>", text)
                    mail_from = m.group(1) if m else text[10:].strip()
                    send("250 OK")
                elif upper.startswith("RCPT TO"):
                    m = re.search(r"<(.*?)>", text)
                    rcpt.append(m.group(1) if m else text[8:].strip())
                    send("250 OK")
                elif upper.startswith("DATA"):
                    send("354 End data with <CR><LF>.<CR><LF>")
                    data = buf[0]
                    buf[0] = b""
                    while not re.search(rb"\r\n\.\r\n\Z", data):
                        chunk = conn.recv(8192)
                        if not chunk:
                            break
                        data += chunk
                    raw = re.sub(rb"\r\n\.\r\n\Z", b"", data)
                    raw = raw.replace(b"\r\n..", b"\r\n.")  # 反转义
                    self._save(raw, mail_from, rcpt)
                    mail_from, rcpt = "", []
                    send("250 OK: queued")
                elif upper.startswith("RSET"):
                    mail_from, rcpt = "", []
                    send("250 OK")
                elif upper.startswith("NOOP"):
                    send("250 OK")
                elif upper.startswith("QUIT"):
                    send("221 Bye")
                    break
                else:
                    send("250 OK")
        except Exception as e:
            print("[收信] 会话异常 %s: %s" % (type(e).__name__, e), flush=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def serve_forever(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(16)
        print("[收信] 监听 %s:%d，邮件落盘到 %s" % (self.host, self.port, self.out_dir), flush=True)
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description="本地 SMTP 收信器（测试用）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2525)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "demo", "_mail_inbox"))
    a = ap.parse_args()
    SmtpSink(a.host, a.port, a.out).serve_forever()


if __name__ == "__main__":
    main()
