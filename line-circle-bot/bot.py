import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

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

# 日本時間
JST = ZoneInfo("Asia/Tokyo")

# 毎週木曜日の何時に出欠確認を送るか
NOTICE_HOUR = 10
NOTICE_MINUTE = 30

# 代表のLINEユーザーID
REPRESENTATIVE_ID = "U84bc6d3ffe464dd9305911304d17c8e2"

# Botが入っているグループID
group_id = "C8c3a162f8f47a2304b7f685b0da44dee"


# =========================
# 自動通知
# =========================

def notification_loop():
    global group_id

    last_sent_date = None

    while True:

        # 日本時間で現在時刻を取得
        now = datetime.now(JST)

        # 木曜日かつ指定時刻になったら送信
        if (
            now.weekday() == 3
            and now.hour == NOTICE_HOUR
            and now.minute == NOTICE_MINUTE
            and last_sent_date != now.date()
        ):

            if group_id is not None:

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
                                    QuickReplyItem(
                                        action=MessageAction(
                                            label="🟢 参加",
                                            text="参加"
                                        )
                                    ),
                                    QuickReplyItem(
                                        action=MessageAction(
                                            label="🔴 不参加",
                                            text="不参加"
                                        )
                                    ),
                                    QuickReplyItem(
                                        action=MessageAction(
                                            label="🟡 遅刻",
                                            text="遅刻"
                                        )
                                    ),
                                ]
                            )
                        )

                        line_bot_api.push_message(
                            PushMessageRequest(
                                to=group_id,
                                messages=[message]
                            )
                        )

                        print("木曜日の出欠確認を自動送信しました！")

                    last_sent_date = now.date()

                except Exception as e:
                    print(f"自動通知でエラーが発生しました: {e}")

            else:
                print("グループIDがまだ取得できていません。")

        # 30秒ごとに確認
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

    # 日本時間の今日の日付
    today = datetime.now(JST).date().isoformat()

    # ユーザーIDを確認
    print(f"USER ID: {user_id}")
    print(f"今日の日付: {today}")

    with ApiClient(configuration) as api_client:

        line_bot_api = MessagingApi(api_client)

        # =========================
        # グループIDを取得
        # =========================

        if event.source.type == "group":

            group_id = event.source.group_id

            print(
                f"グループIDを取得しました: {group_id}"
            )

        # =========================
        # 「予定」
        # =========================

        if text == "予定":

            message = TextMessage(
                text=(
                    "🗓 出欠を選んでください👇\n\n"
                    "※不参加の理由など、連絡事項がある場合は"
                    "代表まで個別に連絡してください。"
                ),
                quick_reply=QuickReply(
                    items=[
                        QuickReplyItem(
                            action=MessageAction(
                                label="🟢 参加",
                                text="参加"
                            )
                        ),
                        QuickReplyItem(
                            action=MessageAction(
                                label="🔴 不参加",
                                text="不参加"
                            )
                        ),
                        QuickReplyItem(
                            action=MessageAction(
                                label="🟡 遅刻",
                                text="遅刻"
                            )
                        ),
                    ]
                )
            )

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[message]
                )
            )


        # =========================
        # 「参加」「不参加」「遅刻」
        # =========================

        elif text in ["参加", "不参加", "遅刻"]:

            try:

                # user_id + event_date をキーにして登録・更新
                supabase.table("attendance").upsert(
                    {
                        "user_id": user_id,
                        "status": text,
                        "event_date": today,
                    },
                    on_conflict="user_id,event_date"
                ).execute()

                # 名前を取得
                if event.source.type == "group":

                    profile = line_bot_api.get_group_member_profile(
                        event.source.group_id,
                        user_id
                    )

                else:

                    profile = line_bot_api.get_profile(
                        user_id
                    )

                name = profile.display_name

                print(
                    f"出欠更新: {name} → {text} "
                    f"({today})"
                )

            except Exception as e:

                print(
                    f"出欠登録でエラーが発生しました: {e}"
                )


        # =========================
        # 「人数」
        # =========================

        elif text == "人数":

            # 代表以外には何もしない
            if user_id != REPRESENTATIVE_ID:
                return

            try:

                # 今日の出欠だけ取得
                result = (
                    supabase
                    .table("attendance")
                    .select("status")
                    .eq("event_date", today)
                    .execute()
                )

                participant_count = 0
                absent_count = 0
                late_count = 0

                for data in result.data:

                    if data["status"] == "参加":
                        participant_count += 1

                    elif data["status"] == "不参加":
                        absent_count += 1

                    elif data["status"] == "遅刻":
                        late_count += 1

                message = TextMessage(
                    text=(
                        "📊 本日の出欠状況\n\n"
                        f"🟢 参加：{participant_count}人\n"
                        f"🔴 不参加：{absent_count}人\n"
                        f"🟡 遅刻：{late_count}人"
                    )
                )

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[message]
                    )
                )

            except Exception as e:

                print(
                    f"人数取得でエラーが発生しました: {e}"
                )


        # =========================
        # 「一覧」
        # =========================

        elif text == "一覧":

            # 代表以外には何もしない
            if user_id != REPRESENTATIVE_ID:
                return

            try:

                # 今日の出欠だけ取得
                result = (
                    supabase
                    .table("attendance")
                    .select("user_id,status")
                    .eq("event_date", today)
                    .execute()
                )

                participant_names = []
                absent_names = []
                late_names = []

                for data in result.data:

                    member_id = data["user_id"]

                    try:

                        if event.source.type == "group":

                            profile = (
                                line_bot_api
                                .get_group_member_profile(
                                    event.source.group_id,
                                    member_id
                                )
                            )

                        else:

                            profile = (
                                line_bot_api
                                .get_profile(member_id)
                            )

                        name = profile.display_name

                    except Exception as e:

                        print(
                            f"名前取得エラー: {member_id} / {e}"
                        )

                        # 名前が取得できない場合
                        name = f"ユーザー({member_id[:8]}...)"

                    if data["status"] == "参加":

                        participant_names.append(name)

                    elif data["status"] == "不参加":

                        absent_names.append(name)

                    elif data["status"] == "遅刻":

                        late_names.append(name)


                # 一覧メッセージ
                message = TextMessage(
                    text=(
                        "📋 本日の出欠一覧\n\n"

                        "🟢 参加\n"
                        +
                        (
                            "\n".join(participant_names)
                            if participant_names
                            else "なし"
                        )

                        + "\n\n🔴 不参加\n"

                        +
                        (
                            "\n".join(absent_names)
                            if absent_names
                            else "なし"
                        )

                        + "\n\n🟡 遅刻\n"

                        +
                        (
                            "\n".join(late_names)
                            if late_names
                            else "なし"
                        )
                    )
                )

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[message]
                    )
                )

            except Exception as e:

                print(
                    f"一覧取得でエラーが発生しました: {e}"
                )


# =========================
# Bot起動
# =========================

if __name__ == "__main__":

    # 自動通知用スレッドを開始
    notification_thread = threading.Thread(
        target=notification_loop,
        daemon=True
    )

    notification_thread.start()

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        debug=False,
        use_reloader=False
    )

