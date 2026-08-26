import requests
from langchain_core.tools import tool


@tool
def get_weather(longitude: str, latitude: str, forecast_days: int = 3):
    """
    功能:使用Open-Meteo查询指定坐标的天气情况。
    Args:
        longitude:经度，例如 "52.520011"
        latitude:纬度，例如 "13.410004"
        forecast_days:预测天数，默认3天
    return:
        指定坐标未来逐小时的温度和相对湿度摘要。
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,relative_humidity_2m",
        "timezone": "auto",
        "forecast_days": forecast_days,
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        return f"天气接口请求失败：{str(e)}"
