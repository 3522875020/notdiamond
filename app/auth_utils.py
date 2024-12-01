import logging
import re
from typing import Dict, Any, Optional

import requests
from requests.exceptions import RequestException

# 常量定义
_BASE_URL = "https://chat.notdiamond.ai"
_API_BASE_URL = "https://spuckhogycrxcbomznwo.supabase.co"
_USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

class AuthManager:
    """
    AuthManager类用于管理身份验证过程,包括获取API密钥、用户信息和处理刷新令牌等操作。
    """
    
    def __init__(self, email: str, password: str):
        self._email: str = email
        self._password: str = password
        self._api_key: str = ""
        self._user_info: Dict[str, Any] = {}
        self._refresh_token: str = ""
        self._session: requests.Session = requests.session()
        
        self._logger: logging.Logger = logging.getLogger(__name__)
        logging.basicConfig(level=logging.INFO)
        
    def login(self) -> None:
        """使用电子邮件和密码进行用户登录,并获取用户信息。"""
        url = f"{_API_BASE_URL}/auth/v1/token?grant_type=password"
        headers = self._get_headers(with_content_type=True)
        data = {
            "email": self._email,
            "password": self._password,
            "gotrue_meta_security": {}
        }

        try:
            response = self._make_request('POST', url, headers=headers, json=data)
            self._user_info = response.json()
            self._refresh_token = self._user_info.get('refresh_token', '')
            self._log_values()
        except RequestException as e:
            self._logger.error(f"\033[91m登录请求错误: {e}\033[0m")

    def refresh_user_token(self) -> None:
        """使用刷新令牌来请求一个新的访问令牌并更新实例变量。"""
        url = f"{_API_BASE_URL}/auth/v1/token?grant_type=refresh_token"
        headers = self._get_headers(with_content_type=True)
        data = {"refresh_token": self._refresh_token}

        try:
            response = self._make_request('POST', url, headers=headers, json=data)
            self._user_info = response.json()
            self._refresh_token = self._user_info.get('refresh_token', '')
            self._log_values()
        except RequestException as e:
            self._logger.error(f"刷新令牌请求错误: {e}")

    def get_jwt_value(self) -> str:
        """返回访问令牌。"""
        return self._user_info.get('access_token', '')

    def _log_values(self) -> None:
        """记录刷新令牌到日志中。"""
        self._logger.info(f"\033[92mRefresh Token: {self._refresh_token}\033[0m")
        
    def _fetch_apikey(self) -> str:
        """获取API密钥。"""
        if self._api_key:
            return self._api_key

        try:
            login_url = f"{_BASE_URL}/login"
            response = self._make_request('GET', login_url)
            
            match = re.search(r'<script src="(/_next/static/chunks/app/layout-[^"]+\.js)"', response.text)
            if not match:
                raise ValueError("未找到匹配的脚本标签")

            js_url = f"{_BASE_URL}{match.group(1)}"
            js_response = self._make_request('GET', js_url)
            
            api_key_match = re.search(r'\("https://spuckhogycrxcbomznwo\.supabase\.co","([^"]+)"\)', js_response.text)
            if not api_key_match:
                raise ValueError("未能匹配API key")
            
            self._api_key = api_key_match.group(1)
            return self._api_key

        except (RequestException, ValueError) as e:
            self._logger.error(f"获取API密钥时发生错误: {e}")
            return ""

    def _get_headers(self, with_content_type: bool = False) -> Dict[str, str]:
        """生成请求头。"""
        headers = {
            'apikey': self._fetch_apikey(),
            'user-agent': _USER_AGENT
        }
        if with_content_type:
            headers['Content-Type'] = 'application/json'
        return headers

    def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """发送HTTP请求并处理异常。"""
        try:
            response = self._session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except RequestException as e:
            self._logger.error(f"请求错误 ({method} {url}): {e}")
            raise
