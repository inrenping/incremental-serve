import base64
import os
from Crypto.Cipher import AES
from Crypto.Hash import MD5
from Crypto.Util.Padding import pad, unpad

class CryptoUtils:
    @staticmethod
    def _evpkdf(password: bytes, salt: bytes, key_len: int = 32, iv_len: int = 16) -> tuple[bytes, bytes]:
        """
        兼容 OpenSSL / CryptoJS 的 EvpKDF 密钥派生算法
        """
        derived = b''
        block = b''
        while len(derived) < (key_len + iv_len):
            block = MD5.new(block + password + salt).digest()
            derived += block
        return derived[:key_len], derived[key_len:key_len + iv_len]

    @classmethod
    def decrypt(cls, encrypted_str: str, secret_key: str) -> str:
        """
        解密 CryptoJS (AES-256-CBC) 加密的 Base64 字符串
        """
        try:
            # 1. 解码 Base64 密文
            encrypted_bytes = base64.b64decode(encrypted_str)
            
            # 2. 验证 CryptoJS 默认的 'Salted__' 头部并提取盐
            if encrypted_bytes[:8] != b'Salted__':
                raise ValueError("Invalid CryptoJS encrypted format (missing 'Salted__' prefix)")
            
            salt = encrypted_bytes[8:16]
            ciphertext = encrypted_bytes[16:]
            
            # 3. 派生 Key 和 IV
            key, iv = cls._evpkdf(secret_key.encode('utf-8'), salt, key_len=32, iv_len=16)
            
            # 4. AES CBC 解密并去填充
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted_padded = cipher.decrypt(ciphertext)
            decrypted_bytes = unpad(decrypted_padded, AES.block_size)
            
            return decrypted_bytes.decode('utf-8')
        except Exception as e:
            raise ValueError(f"Decryption failed: {str(e)}")

    @classmethod
    def encrypt(cls, plain_text: str, secret_key: str) -> str:
        """
        将明文加密为兼容 CryptoJS 的 Base64 字符串
        """
        try:
            # 1. 随机生成 8 字节的盐
            salt = os.urandom(8)
            
            # 2. 派生 Key 和 IV
            key, iv = cls._evpkdf(secret_key.encode('utf-8'), salt, key_len=32, iv_len=16)
            
            # 3. AES CBC 加密并填充
            cipher = AES.new(key, AES.MODE_CBC, iv)
            padded_data = pad(plain_text.encode('utf-8'), AES.block_size)
            ciphertext = cipher.encrypt(padded_data)
            
            # 4. 拼接 'Salted__' + 盐 + 密文，并转为 Base64
            result_bytes = b'Salted__' + salt + ciphertext
            return base64.b64encode(result_bytes).decode('utf-8')
        except Exception as e:
            raise ValueError(f"Encryption failed: {str(e)}")