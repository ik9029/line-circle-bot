import os
import threading
import time
from datetime import datetime

from flask import Flask, request
from dotenv import load_dotenv
from supabase import create_client, Client

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
)
from linebot.v3.messaging.models import MessageAction
from linebot.v3.webhooks import MessageEvent, TextMessageContent

load_dotenv()

app = Flask(__name__)

# =========================
# LINE設定
# =========================
configuration = Configuration(
    access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
)
handler = WebhookHandler(
    os.getenv("LINE_CHANNEL_SECRET")
)

# =========================
# Supabase設定
# =========================
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SECRET_KEY")
)

# =========================
# 設定
# =========================
NOTICE_HOUR = 10
NOTICE_MINUTE = 30
REPRESENTATIVE_ID = "U84bc6d3ffe464dd9305911304d17c8e2"
group_id = "C8c3a162f8f47a2304b7f685b0da44dee"


# =========================
# 自動通知
# =========================
def notification_loop():
    global group_id
    last_sent_date = None

    while True:
        now = datetime.now()

        # 木曜日(weekday == 3) の指定時刻に送信
        if (
            now.weekday() == 3
            and now.hour == NOTICE_HOUR
            and now.minute == NOTICE_MINUTE
            and last_sent_date != now.date()
        ):
            if group_id:
                try:
                    with ApiClient(configuration) as api_client:
                        line_bot_api = MessagingApi(api_client)
                        message = TextMessage(
                            text=(
                                "🏸 本日のサークル出欠確認\n\n"
                                "今日の参加状況を入力してください👇"
                            ),
                            quick_reply=QuickReply(
                                items=[
                                    QuickReplyItem(action=MessageAction(label="🟢 参加", text="参加")),
                                    QuickReplyItem(action=MessageAction(label="🔴 不参加", text="不参加")),
                                    QuickReplyItem(action=MessageAction(label="🟡 遅刻", text="遅刻")),
                                ]
                            )
                        )
                        line_bot_api.push_message(
                            PushMessageRequest(to=group_id, messages=[message])
                        )
                        print("木曜日の出欠確認を自動送信しました！")
                        last_sent_date = now.date()
                except Exception as e:
                    print(f"自動送信エラー: {e}")
            else:
                print("グループIDがまだ取得できていません。")

        time.sleep(30)


# =========================
# Webhook
# =========================
@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400

    return "OK", 200


# =========================
# メッセージ処理
# =========================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    global group_id

    text = event.message.text
    user_id = event.source.user_id
    today = datetime.now().strftime("%Y-%m-%d")  # 本日の日付 (例: "2026-09-11")

    print(f"USER ID: {user_id}, DATE: {today}")

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        if event.source.type == "group":
            group_id = event.source.group_id
            print(f"グループIDを取得しました: {group_id}")

        # --- 「予定」 ---
        if text == "予定":
            message = TextMessage(
                text=(
                    "🗓 出欠を選んでください👇\n\n"
                    "※不参加の理由など、連絡事項がある場合は"
                    "代表まで個別に連絡してください。"
                ),
                quick_reply=QuickReply(
                    items=[
                        QuickReplyItem(action=MessageAction(label="🟢 参加", text="参加")),
                        QuickReplyItem(action=MessageAction(label="🔴 不参加", text="不参加")),
                        QuickReplyItem(action=MessageAction(label="🟡 遅刻", text="遅刻")),
                    ]
                )
            )
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[message])
            )

        # --- 「参加」「不参加」「遅刻」 ---
        elif text in ["参加", "不参加", "遅刻"]:
            # 「本日の日付」かつ「送信したユーザー」のレコードがあるか検索
            existing = (
                supabase.table("attendance")
                .select("id")
                .eq("user_id", user_id)
                .eq("date", today)
                .execute()
            )

            if existing.data:
                # 既に本日分があればステータスを更新
                supabase.table("attendance").update({"status": text}).eq("user_id", user_id).eq("date", today).execute()
            else:
                # 本日分がなければ新規追加
                supabase.table("attendance").insert({"user_id": user_id, "status": text, "date": today}).execute()

            # プロフィール取得（失敗してもエラーで止めない）
            try:
                if event.source.type == "group":
                    profile = line_bot_api.get_group_member_profile(event.source.group_id, user_id)
                else:
                    profile = line_bot_api.get_profile(user_id)
                name = profile.display_name
            except Exception:
                name = user_id

            print(f"出欠更新 ({today}): {name} → {text}")

        # --- 「人数」 ---
        elif text == "人数":
            if user_id != REPRESENTATIVE_ID:
                return

            # 本日の出欠状況のみを取得
            result = supabase.table("attendance").select("status").eq("date", today).execute()

            counts = {"参加": 0, "不参加": 0, "遅刻": 0}
            for row in result.data:
                st = row.get("status")
                if st in counts:
                    counts[st] += 1

            message = TextMessage(
                text=(
                    f"📊 本日({today})の出欠状況\n\n"
                    f"🟢 参加：{counts['参加']}人\n"
                    f"🔴 不参加：{counts['不参加']}人\n"
                    f"🟡 遅刻：{counts['遅刻']}人"
                )
            )
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[message])
            )

        # --- 「一覧」 ---
        elif text == "一覧":
            if user_id != REPRESENTATIVE_ID:
                return

            # 本日の出欠一覧を取得
            result = supabase.table("attendance").select("user_id, status").eq("date", today).execute()

            members = {"参加": [], "不参加": [], "遅刻": []}

            for row in result.data:
                member_id = row["user_id"]
                st = row.get("status")

                try:
                    if event.source.type == "group":
                        profile = line_bot_api.get_group_member_profile(event.source.group_id, member_id)
                    else:
                        profile = line_bot_api.get_profile(member_id)
                    name = profile.display_name
                except Exception:
                    name = f"ユーザー({member_id[:6]}...)"

                if st in members:
                    members[st].append(name)

            message_text = (
                f"📋 本日({today})の出欠一覧\n\n"
                f"🟢 参加\n{'\n'.join(members['参加']) if members['参加'] else 'なし'}\n\n"
                f"🔴 不参加\n{'\n'.join(members['不参加']) if members['不参加'] else 'なし'}\n\n"
                f"🟡 遅刻\n{'\n'.join(members['遅刻']) if members['遅刻'] else 'なし'}"
            )

            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=message_text)])
            )


# =========================
# Bot起動
# =========================
if __name__ == "__main__":
    notification_thread = threading.Thread(target=notification_loop, daemon=True)
    notification_thread.start()

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        debug=False,
        use_reloader=False
    )