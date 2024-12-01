import json
import uuid
import time
import tiktoken
from constants import CHAT_COMPLETION_CHUNK, CONTENT_TYPE_EVENT_STREAM
from flask import jsonify
import logging

logger = logging.getLogger(__name__)

# 缓存tokenizer实例
_TOKENIZERS = {}

def get_tokenizer(model="gpt-3.5-turbo-0301"):
    """获取或创建tokenizer实例"""
    if model not in _TOKENIZERS:
        try:
            _TOKENIZERS[model] = tiktoken.encoding_for_model(model)
        except KeyError:
            _TOKENIZERS[model] = tiktoken.get_encoding("cl100k_base")
    return _TOKENIZERS[model]

def generate_system_fingerprint():
    """生成并返回唯一的系统指纹。"""
    return f"fp_{uuid.uuid4().hex[:10]}"

def create_openai_chunk(content, model, finish_reason=None, usage=None):
    """创建格式化的 OpenAI 响应块。"""
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": CHAT_COMPLETION_CHUNK,
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": generate_system_fingerprint(),
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "logprobs": None,
                "finish_reason": finish_reason
            }
        ]
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk

def count_tokens(text, model="gpt-3.5-turbo-0301"):
    """计算给定文本的令牌数量。"""
    return len(get_tokenizer(model).encode(text))

def count_message_tokens(messages, model="gpt-3.5-turbo-0301"):
    """计算消息列表中的总令牌数量。"""
    return sum(count_tokens(str(message), model) for message in messages)

def stream_notdiamond_response(response, model):
    """流式处理 notdiamond API 响应。"""
    buffer = ""
    
    try:
        for chunk in response.iter_content(1024):
            if not chunk:
                continue
                
            buffer += chunk.decode('utf-8')
            
            # 处理可能的不完整JSON
            try:
                yield create_openai_chunk(buffer, model)
                buffer = ""
            except json.JSONDecodeError:
                continue
    except Exception as e:
        logger.error(f"Error in stream processing: {str(e)}")
        yield create_openai_chunk("", model, 'error')
    finally:
        if buffer:  # 处理剩余的buffer
            try:
                yield create_openai_chunk(buffer, model)
            except:
                pass
        yield create_openai_chunk('', model, 'stop')

def handle_non_stream_response(response, model, prompt_tokens):
    """处理非流式 API 响应并构建最终 JSON。"""
    full_content = ""
    
    for chunk in stream_notdiamond_response(response, model):
        if chunk['choices'][0]['delta'].get('content'):
            full_content += chunk['choices'][0]['delta']['content']

    completion_tokens = count_tokens(full_content, model)
    total_tokens = prompt_tokens + completion_tokens

    return jsonify({
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": generate_system_fingerprint(),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_content
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens
        }
    })

def generate_stream_response(response, model, prompt_tokens):
    """生成流式 HTTP 响应。"""
    total_completion_tokens = 0
    
    for chunk in stream_notdiamond_response(response, model):
        content = chunk['choices'][0]['delta'].get('content', '')
        total_completion_tokens += count_tokens(content, model)
        
        chunk['usage'] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": prompt_tokens + total_completion_tokens
        }
        
        yield f"data: {json.dumps(chunk)}\n\n"
    
    yield "data: [DONE]\n\n"