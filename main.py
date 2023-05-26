
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import *
import os
import uuid

import pandas as pd

from src.models import OpenAIModel
from src.memory import Memory
from src.logger import logger
from src.storage import Storage, FileStorage, MongoStorage
from src.utils import get_role_and_content
from src.service.youtube import Youtube, YoutubeTranscriptReader
from src.service.website import Website, WebsiteReader
from src.mongodb import mongodb

load_dotenv('.env')

app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
storage = None
youtube = Youtube(step=4)
website = Website()


memory = Memory(system_message=os.getenv('SYSTEM_MESSAGE'), memory_message_count=2)
model_management = {}
api_keys = {}
chat=True
place_array=["士林","士林區","大同","大同區","信義","信義區","北投","北投區","文山","文山區","大安","大安區","中正","中正區","內湖","內湖區","松山","松山區","中山","中山區"]
user_states = {}
user_messages = {}
assistant_messages = {}
MAX_CHARS = 150
user_next_indices = {}  # 追蹤每位用戶已經發送的訊息字數
place_array = ["使用者","關係人","關係人的電話"]

workbook = Workbook()
worksheet = workbook.active

def save_to_excel(user_input):
    column = get_column_letter(len(worksheet[1]) + 1)
    worksheet[column + '1'] = "使用者輸入"
    row = len(worksheet[column]) + 1
    worksheet[column + str(row)] = user_input
    workbook.save("data.xlsx")

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    return 'OK'

def generate_summary(conversation):
    return " ".join(conversation[:10])

def generate_reply_messages(response, user_id):
    response_len = len(response)
    remaining_response = response
    messages = []
    while response_len > MAX_CHARS:
        split_index = remaining_response.rfind(' ', 0, MAX_CHARS)
        current_message = remaining_response[:split_index]
        remaining_response = remaining_response[split_index + 1:]
        response_len = len(remaining_response)
        messages.append(TextSendMessage(text=current_message, quick_reply=QuickReply(items=[QuickReplyButton(action=MessageAction(label="繼續", text="繼續"))])))
    messages.append(TextSendMessage(text=remaining_response))
    user_next_indices[user_id] = len(user_messages[user_id])
    return messages

@handler.add(MessageEvent, message=TextMessage)

def handle_text_message(event):
    global chat
    user_id = event.source.user_id
    text = event.message.text.strip()
    logger.info(f'{user_id}: {text}')
    api_key = os.getenv("CHATGPT_API_KEY")
    model = OpenAIModel(api_key=api_key)
    is_successful, _, _ = model.check_token_valid()
    if not is_successful:
        raise ValueError('Invalid API token')
    model_management[user_id] = model
    storage.save({
        user_id: api_key
    })
    if user_id not in user_messages:
        user_messages[user_id] = []

    if user_id not in assistant_messages:
        assistant_messages[user_id] = []

    user_messages[user_id].append(text)

    if user_id not in user_next_indices:
        user_next_indices[user_id] = 0

    try:
        
        if text=='emo你在嗎':
            msg = TextSendMessage(
                text="我在，有甚麼可以幫您的嗎，以下是您可以使用的指令\n\n指令：\n\n忘記\n👉 Emo會忘記上下文關係，接下來的回答不再跟上文有關係~\n\n請畫\n👉 請畫+你想畫的東西 Emo會在短時間畫給你~\n\n語音輸入\n👉 使用line語音輸入Emo可以直接回覆喔~\n\n其他文字輸入\n👉 Emo直接以文字回覆~",
                quick_reply=QuickReply(
                items=[
                    QuickReplyButton(
                    action=MessageAction(label="忘記", text="忘記")
                    ),
                    QuickReplyButton(
                    action=MessageAction(label="請畫", text="請畫")
                    ),
                    QuickReplyButton(
                    action=MessageAction(label="總結", text="總結")
                    ),
                    QuickReplyButton(
                    action=MessageAction(label="語音輸入", text="語音輸入")
                    ),
                ]                      
            )
        )

        elif text == '總結':
            conversation = user_messages[user_id] + assistant_messages[user_id]
            summary = generate_summary(conversation)
            msg = TextSendMessage(text=summary)

        elif text=='忘記':
            memory.remove(user_id)
            msg = TextSendMessage(text='歷史訊息清除成功')

        elif text == '請畫':
            user_states[user_id] = 'drawing'
            msg = TextSendMessage(text='請輸入你想畫的東西')

        elif user_states.get(user_id) == 'drawing':
            prompt = text.strip()
            memory.append(user_id, 'user', prompt)
            is_successful, response, error_message = model_management[user_id].image_generations(prompt)
            if not is_successful:
                raise Exception(error_message)
            url = response['data'][0]['url']
            msg = ImageSendMessage(
                original_content_url=url,
                preview_image_url=url
            )
            memory.append(user_id, 'assistant', url)

            user_states[user_id] = None

        elif text=="語音輸入":
            msg=TextSendMessage(
                    text="請選擇輸出方式",
                    quick_reply=QuickReply(
                    items=[
                        QuickReplyButton(
                            action=MessageAction(label="文字", text="文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="語音", text="語音")
                        ),
                    ]
                )
            )
        elif text=="文字":
            msg=TextSendMessage(text="可以用語音跟emo聊天嘍~")

        elif text=="語音":
            msg=TextSendMessage(text="近期即將推出，敬請期待")

        else:
            if text=='開啟自動回覆':
                chat=True

            elif text=='關閉自動回覆':
                chat=False

            elif text=='我想要查詢心理醫療機構':
                msg=TextSendMessage(
                    text="請點選想查詢的地區",
                    quick_reply=QuickReply(
                    items=[
                        QuickReplyButton(
                            action=MessageAction(label="士林區", text="士林區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="大同區", text="大同區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="信義區", text="信義區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="北投區", text="北投區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="文山區", text="文山區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="大安區", text="大安區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="中正區", text="中正區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="內湖區", text="內湖區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="松山區", text="松山區")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="中山區", text="中山區")
                        )

                        ]
                    )
                )

            elif text=='我想要做心理測驗':
                pass

            elif text in place_array:
                pass

            elif text in place_array:
                # 呼叫儲存到 Excel 表的函式
                save_to_excel(text)
                msg = TextSendMessage(text='已儲存到 Excel 表中')

            elif chat==True:
                user_model = model_management[user_id]
                memory.append(user_id, 'user', text)
                url = website.get_url_from_text(text)
                if url:
                    if youtube.retrieve_video_id(text):
                        is_successful, chunks, error_message = youtube.get_transcript_chunks(youtube.retrieve_video_id(text))
                        if not is_successful:
                            raise Exception(error_message)
                        youtube_transcript_reader = YoutubeTranscriptReader(user_model, os.getenv('OPENAI_MODEL_ENGINE'))
                        is_successful, response, error_message = youtube_transcript_reader.summarize(chunks)
                        if not is_successful:
                            raise Exception(error_message)
                        role, response = get_role_and_content(response)
                        msg = TextSendMessage(text=response)
                    else:
                        chunks = website.get_content_from_url(url)
                        if len(chunks) == 0:
                            raise Exception('無法撈取此網站文字')
                        website_reader = WebsiteReader(user_model, os.getenv('OPENAI_MODEL_ENGINE'))
                        is_successful, response, error_message = website_reader.summarize(chunks)
                        if not is_successful:
                            raise Exception(error_message)
                        role, response = get_role_and_content(response)
                        msg = TextSendMessage(text=response)
                else:
                    is_successful, response, error_message = user_model.chat_completions(memory.get(user_id), os.getenv('OPENAI_MODEL_ENGINE'))
                    if not is_successful:
                        raise Exception(error_message)
                    role, response = get_role_and_content(response)
                    if len(response) > MAX_CHARS:
                        messages = generate_reply_messages(response, user_id)
                        line_bot_api.reply_message(event.reply_token, messages)
                        return 'OK'
                memory.append(user_id, role, response)
                msg = TextSendMessage(text=response)
                    
               
    except ValueError:
        msg = TextSendMessage(text='Token 無效，請重新註冊，格式為 /註冊 sk-xxxxx')
    except KeyError:
        msg = TextSendMessage(text='錯誤')
    except Exception as e:
        memory.remove(user_id)
        if str(e).startswith('Incorrect API key provided'):
            msg = TextSendMessage(text='OpenAI API Token 有誤，請重新註冊。')
        elif str(e).startswith('That model is currently overloaded with other requests.'):
            msg = TextSendMessage(text='已超過負荷，請稍後再試')
        else:
            msg = TextSendMessage(text=str(e))
    line_bot_api.reply_message(event.reply_token, msg)


@handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event):
    user_id = event.source.user_id
    audio_content = line_bot_api.get_message_content(event.message.id)
    input_audio_path = f'{str(uuid.uuid4())}.m4a'
    with open(input_audio_path, 'wb') as fd:
        for chunk in audio_content.iter_content():
            fd.write(chunk)

    try:
        if not model_management.get(user_id):
            raise ValueError('Invalid API token')
        else:
            is_successful, response, error_message = model_management[user_id].audio_transcriptions(input_audio_path, 'whisper-1')
            if not is_successful:
                raise Exception(error_message)
            memory.append(user_id, 'user', response['text'])
            is_successful, response, error_message = model_management[user_id].chat_completions(memory.get(user_id), 'gpt-3.5-turbo')
            if not is_successful:
                raise Exception(error_message)
            role, response = get_role_and_content(response)
            memory.append(user_id, role, response)
            msg = TextSendMessage(text=response)
    except ValueError:
        msg = TextSendMessage(text='請先註冊你的 API Token，格式為 /註冊 [API TOKEN]')
    except KeyError:
        msg = TextSendMessage(text='請先註冊 Token，格式為 /註冊 sk-xxxxx')
    except Exception as e:
        memory.remove(user_id)
        if str(e).startswith('Incorrect API key provided'):
            msg = TextSendMessage(text='OpenAI API Token 有誤，請重新註冊。')
        else:
            msg = TextSendMessage(text=str(e))
    os.remove(input_audio_path)
    line_bot_api.reply_message(event.reply_token, msg)


@app.route("/", methods=['GET'])
def home():
    return 'Hello World'


if __name__ == "__main__":
    if os.getenv('USE_MONGO'):
        mongodb.connect_to_database()
        storage = Storage(MongoStorage(mongodb.db))
    else:
        storage = Storage(FileStorage('db.json'))
    try:
        data = storage.load()
        for user_id in data.keys():
            model_management[user_id] = OpenAIModel(api_key=data[user_id])
    except FileNotFoundError:
        pass
    app.run(host='0.0.0.0', port=8080)
