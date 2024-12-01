import logging
import os
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from json import JSONDecodeError

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

from .auth_utils import AuthManager
from .constants import (
    CONTENT_TYPE_EVENT_STREAM,
    DEFAULT_AUTH_EMAIL,
    DEFAULT_AUTH_PASSWORD,
    DEFAULT_NOTDIAMOND_URL,
    DEFAULT_PORT,
    DEFAULT_TEMPERATURE,
    MAX_WORKERS,
    SYSTEM_MESSAGE_CONTENT,
    USER_AGENT,
    API_PREFIX,
)
from .model_info import MODEL_INFO
from .utils import count_message_tokens, handle_non_stream_response, generate_stream_response

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 初始化 Flask 应用
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# 初始化线程池和其他全局变量
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
proxy_url = os.getenv('PROXY_URL')
NOTDIAMOND_URLS = os.getenv('NOTDIAMOND_URLS', DEFAULT_NOTDIAMOND_URL).split(',')
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', 30))  # 请求超时时间

# 确保程序退出时关闭线程池
import atexit
atexit.register(lambda: executor.shutdown(wait=True))

# 初始化认证管理器
auth_manager = AuthManager(
    os.getenv("AUTH_EMAIL", DEFAULT_AUTH_EMAIL),
    os.getenv("AUTH_PASSWORD", DEFAULT_AUTH_PASSWORD),
)

def get_notdiamond_url():
    """随机选择并返回一个 notdiamond URL。"""
    url = random.choice(NOTDIAMOND_URLS)
    logger.info(f"Selected NotDiamond URL: {url}")
    return url

def get_notdiamond_headers():
    """返回用于 notdiamond API 请求的头信息。"""
    jwt = auth_manager.get_jwt_value()
    if not jwt:
        auth_manager.login()
        jwt = auth_manager.get_jwt_value()
    
    return {
        'accept': CONTENT_TYPE_EVENT_STREAM,
        'accept-language': 'zh-CN,zh;q=0.9',
        'content-type': 'application/json',
        'user-agent': USER_AGENT,
        'authorization': f'Bearer {jwt}'
    }

def build_payload(request_data, model_id):
    """构建请求有效负载。"""
    messages = request_data.get('messages', [])
    
    if not any(message.get('role') == 'system' for message in messages):
        system_message = {
            "role": "system",
            "content": SYSTEM_MESSAGE_CONTENT
        }
        messages.insert(0, system_message)
    
    mapping = MODEL_INFO.get(model_id, {}).get('mapping', model_id)
    payload = {
        key: value for key, value in request_data.items() 
        if key not in ('stream',)
    }
    payload['messages'] = messages
    payload['model'] = mapping
    payload['temperature'] = request_data.get('temperature', DEFAULT_TEMPERATURE)
    
    return payload

def make_request(payload):
    """发送请求并处理可能的认证刷新。"""
    url = get_notdiamond_url()
    last_error = None
    
    for attempt in range(3):  # 最多尝试3次
        try:
            headers = get_notdiamond_headers()
            # 添加日志来调试请求
            logger.info(f"Sending request to URL: {url}")
            logger.info(f"Request headers: {headers}")
            logger.info(f"Request payload: {payload}")
            
            response = executor.submit(
                requests.post, 
                url, 
                headers=headers, 
                json=payload, 
                stream=True,
                timeout=REQUEST_TIMEOUT
            ).result()

            # 添加响应日志
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response headers: {dict(response.headers)}")

            if response.status_code == 200:
                return response
            elif response.status_code == 401:  # 认证错误
                logger.info("Authentication failed, refreshing token...")
                auth_manager.refresh_user_token()
                continue
            elif response.status_code == 404:  # 添加404错误的具体处理
                logger.error(f"API endpoint not found: {url}")
                last_error = f"API endpoint not found: {url}"
                break
                
            last_error = f"HTTP {response.status_code}: {response.text}"
            
        except requests.Timeout:
            last_error = "Request timed out"
            logger.warning(f"Request timeout on attempt {attempt + 1}")
            continue
            
        except requests.RequestException as e:
            last_error = str(e)
            logger.error(f"Request error on attempt {attempt + 1}: {e}")
            if attempt < 2:  # 如果不是最后一次尝试，继续重试
                continue
            break
    
    raise requests.RequestException(f"All attempts failed. Last error: {last_error}")

@app.route(f"{API_PREFIX}/v1/models", methods=['GET'])
def proxy_models():
    """返回可用模型列表。"""
    models = [
        {
            "id": model_id,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "openai",
            "permission": [
                {
                    "id": f"modelperm-{uuid.uuid4().hex[:24]}",
                    "object": "model_permission",
                    "created": int(time.time()),
                    "allow_create_engine": True,
                    "allow_sampling": True,
                    "allow_logprobs": True,
                    "allow_search_indices": False,
                    "allow_view": True,
                    "allow_fine_tuning": False,
                    "organization": "*",
                    "group": None,
                    "is_blocking": False
                }
            ],
            "root": model_id,
            "parent": None,
        } for model_id in MODEL_INFO.keys()
    ]
    return jsonify({
        "object": "list",
        "data": models
    })

@app.route(f"{API_PREFIX}/v1/chat/completions", methods=['POST'])
def handle_request():
    """处理聊天完成请求。"""
    try:
        request_data = request.get_json()
        logger.info(f"Received request data: {request_data}")
        
        model_id = request_data.get('model', '')
        logger.info(f"Requested model: {model_id}")
        
        if model_id not in MODEL_INFO:
            logger.error(f"Unsupported model: {model_id}")
            return jsonify({
                'error': {
                    'message': '模型不支持,通过v1/models获取支持的模型列表',
                    'type': 'invalid_request_error',
                    'param': 'model',
                    'code': 'unsupported_model',
                    'details': f'The model_id {model_id} is not recognized'
                }
            }), 400

        stream = request_data.get('stream', False)
        logger.info(f"Stream mode: {stream}")

        prompt_tokens = count_message_tokens(
            request_data.get('messages', []),
            model_id
        )
        logger.info(f"Prompt tokens: {prompt_tokens}")

        payload = build_payload(request_data, model_id)
        logger.info(f"Built payload: {payload}")
        
        # 检查环境变量
        logger.info(f"NOTDIAMOND_URLS environment variable: {os.getenv('NOTDIAMOND_URLS')}")
        
        response = make_request(payload)

        if stream:
            return Response(
                stream_with_context(generate_stream_response(response, model_id, prompt_tokens)),
                content_type=CONTENT_TYPE_EVENT_STREAM
            )
        else:
            return handle_non_stream_response(response, model_id, prompt_tokens)
    
    except requests.RequestException as e:
        logger.error("Request error: %s", str(e), exc_info=True)
        return jsonify({
            'error': {
                'message': 'Error communicating with the API',
                'type': 'api_error',
                'param': None,
                'code': None,
                'details': str(e)
            }
        }), 503
    except JSONDecodeError as e:
        logger.error("JSON decode error: %s", str(e), exc_info=True)
        return jsonify({
            'error': {
                'message': 'Invalid JSON in request',
                'type': 'invalid_request_error',
                'param': None,
                'code': 'invalid_json',
                'details': str(e)
            }
        }), 400
    except Exception as e:
        logger.error("Unexpected error: %s", str(e), exc_info=True)
        return jsonify({
            'error': {
                'message': 'Internal Server Error',
                'type': 'server_error',
                'param': None,
                'code': None,
                'details': str(e)
            }
        }), 500

@app.before_request
def log_request_info():
    logger.info(f"收到 {request.method} 请求到 {request.url}")
    logger.debug(f"请求头：{dict(request.headers)}")
    logger.debug(f"请求体：{request.get_data()}")

@app.after_request
def log_response_info(response):
    logger.info(f"响应状态码 {response.status}")
    logger.debug(f"响应头：{dict(response.headers)}")
    return response

if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    app.run(debug=False, host='0.0.0.0', port=port, threaded=True)
