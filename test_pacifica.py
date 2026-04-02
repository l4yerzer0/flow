import requests

response = requests.get("https://api.pacifica.fi/api/v1/info")
print(response.status_code)
try:
    data = response.json()
    print("Keys:", data.keys())
    if "data" in data and len(data["data"]) > 0:
        print("First market:", data["data"][0])
    elif "markets" in data:
        print("First market:", data["markets"][0])
    else:
        print(data)
except Exception as e:
    print(e)
