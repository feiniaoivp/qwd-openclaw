import requests
import ssl
import sys

# Test the SSL connection issue
try:
    ssl_context = ssl.create_default_context()
    ssl_context.set_ciphers('DEFAULT@SECLEVEL=1')
    ssl_context.options |= ssl.OPT_LEGACY_SERVER_CONNECT
    
    response = requests.get(
        'https://stock.finance.sina.com.cn/stock/api/jsonp.php/var%20KKE_ShareAmount_sz000157=/StockService.getAmountBySymbol?_=20&symbol=sz000157',
        verify=True,
        timeout=10
    )
    print(f"Success: {response.status_code}")
    print(response.text[:500])
except Exception as e:
    print(f"Error: {e}")