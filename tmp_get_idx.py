import requests
idx_codes = 's_sh000001,s_sz399001,s_sz399006'
headers = {'Referer': 'https://finance.sina.com.cn'}
url = f'https://hq.sinajs.cn/list={idx_codes}'
resp = requests.get(url, headers=headers, timeout=10)
resp.encoding = 'gbk'
for line in resp.text.strip().split('\n'):
    if '=' not in line: continue
    vals = line.split('=')[1].strip().strip(';').strip('"').split(',')
    if len(vals) >= 4:
        print(f'{vals[0]} 现价:{vals[1]} 涨跌:{vals[2]} ({vals[3]}%)')