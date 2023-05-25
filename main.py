
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

    try:
        
        if text.startswith('/指令說明'):
            msg = TextSendMessage(text="指令：\n/清除\n👉 當前每一次都會紀錄最後兩筆歷史紀錄，這個指令能夠清除歷史訊息\n\n/畫圖 + Prompt\n👉 會調用 DALL∙E 2 Model，以文字生成圖像\n\n語音輸入\n👉 會調用 Whisper 模型，先將語音轉換成文字，再調用 ChatGPT 以文字回覆\n\n其他文字輸入\n👉 調用 ChatGPT 以文字回覆")

        elif text.startswith('/忘記'):
            memory.remove(user_id)
            msg = TextSendMessage(text='歷史訊息清除成功')

        elif text.startswith('/畫圖'):
            prompt = text[3:].strip()
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

        else:
            if text=='開啟自動回覆':
                chat=True
            elif text=='關閉自動回覆':
                chat=False
            elif text=='我想要查詢心理醫療機構':
                msg=TextSendMessage(
                    text="你好",
                    quick_reply=QuickReply(
                    items=[
                        QuickReplyButton(
                            action=MessageAction(label="士林區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="大同區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="信義區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="北投區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="文山區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="大安區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="中正區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="內湖區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="松山區", text="回傳文字")
                        ),
                        QuickReplyButton(
                            action=MessageAction(label="中山區", text="回傳文字")
                        )

                        ]
                    )
                )
            elif text=='我想要做心理測驗':
                pass

            elif text in place_array:
                pass

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
                    msg = TextSendMessage(text=response)
                memory.append(user_id, role, response)
    except ValueError:
        msg = TextSendMessage(text='Token 無效，請重新註冊，格式為 /註冊 sk-xxxxx')
    except KeyError:
        msg = TextSendMessage(text='請先註冊 Token，格式為 /註冊 sk-xxxxx')
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
