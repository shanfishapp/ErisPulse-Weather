import aiohttp
import asyncio
from ErisPulse import sdk

class Main:
    def __init__(self):
        self.sdk = sdk
        self.logger = sdk.logger
        self.adapter = sdk.adapter
        self.data = None  # 新增一个实例变量来存储当前的data
        self.env = sdk.env  # 添加环境变量操作接口
        
        self._register_handlers()
        
    @staticmethod
    def should_eager_load() -> bool:
        return True
    
    def _register_handlers(self):
        self.adapter.on("message")(self._handle_message)
        self.logger.info("天气获取 模块已成功注册事件")

    async def _handle_message(self, data):
        if not data.get("alt_message"):
            return
            
        text = data.get("alt_message", "").strip().lower()
        if text.startswith("天气") or text.startswith("/天气"):
            self.data = data  # 将data存储为实例变量
            asyncio.create_task(self._handle_request())  # 移除参数，因为现在可以通过self访问

    async def _get_adapter_sender(self):
        if not self.data:
            self.logger.warning("没有可用的消息数据")
            return None
            
        detail_type = self.data.get("detail_type", "private")
        datail_id = self.data.get("user_id") if detail_type == "private" else self.data.get("group_id")
        adapter_name = self.data.get("self", {}).get("platform", None)
        
        self.logger.info(f"获取到消息来源: {adapter_name} {detail_type} {datail_id}")
        if not adapter_name:
            self.logger.warning("无法获取消息来源平台")
            
        adapter = getattr(self.sdk.adapter, adapter_name)
        return adapter.Send.To("user" if detail_type == "private" else "group", datail_id)

    async def _handle_request(self):
        if not self.data:
            self.logger.warning("没有可用的消息数据")
            return
            
        msg = self.data.get("alt_message", "").lstrip("/").replace("天气", "", 1).strip()
        if msg.startswith("绑定"):
            return await self._bind_user_city(msg)
        elif msg.startswith("今日"):
            return await self._today_weather(msg)
        elif msg.startswith("五日"):
            return await self._five_day_weather(msg)
        elif msg.startswith("解绑"):
            return await self._unbind_user_city()
        elif msg.startswith("查绑"):
            return await self._show_binded()
        else:
            return await self._unknown_command(msg)
    
    async def _show_binded(self):
        user_id = self.data.get("user_id")
        city = self.env.get(f"weather:{user_id}", "")
        sender = await self._get_adapter_sender()
        try:
            if city:
                await sender.Text(f"您当前绑定的城市为：{city}")
            else:
                await sender.Text("目前没有绑定城市")
        except Exception as e:
            await sender.Text(f"天气查绑失败：{str(e)}")
    
    async def _bind_user_city(self, msg):
        """绑定用户城市"""
        sender = await self._get_adapter_sender()
        try:
            city = msg.replace("绑定", "", 1).strip()
            if not city:
                await sender.Text("请提供要绑定的城市名称，例如：/天气 绑定 北京")
                return
            
            user_id = self.data.get("user_id")
            self.env.set(f"weather:{user_id}", city)
            await sender.Text(f"成功绑定您的默认城市为: {city}\n以后可以直接使用'/天气 今日'或'/天气 五日'来查询")
        except Exception as e:
            await sender.Text(f"绑定城市失败: {str(e)}")
    
    async def _unbind_user_city(self):
        user_id = self.data.get("user_id")
        city = self.env.get(f"weather:{user_id}", "")
        sender = await self._get_adapter_sender()
        try:
            if not city:
                await sender.Text("目前没有绑定城市")
                return
            self.env.delete(f"weather:{user_id}")
            await sender.Text(f"成功删除当前绑定的城市：{city}")
        except Exception as e:
            await sender.Text(f"解绑城市失败：{str(e)}")
        
    async def _get_city_name(self, msg):
        """获取城市名称，优先使用传入的，其次使用绑定的"""
        # 提取命令后的城市名称
        command = msg.split()[0] if msg else ""
        city = msg.replace(command, "", 1).strip()
        
        # 如果用户没有输入城市，尝试获取绑定的城市
        if not city:
            user_id = self.data.get("user_id")
            city = self.env.get(f"weather:{user_id}", "")
            if not city:
                return None, "您还没有绑定默认城市，请使用'/天气 绑定 城市名称'绑定，或在命令后加上城市名称"
        
        return city, None
    
    async def _unknown_command(self, msg):
        self.logger.warning(f"触发未知命令：{msg}")
        sender = await self._get_adapter_sender()
        return await sender.Text(f"触发未知命令：{msg}\n可用命令：\n/天气 今日 [城市]\n/天气 五日 [城市]\n/天气 绑定 城市\n/天气 解绑\n/天气 查绑")
        
    async def _today_weather(self, msg):
        sender = await self._get_adapter_sender()
        try:
            city, error = await self._get_city_name(msg)
            if error:
                await sender.Text(error)
                return
                
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.52vmy.cn/api/query/tian?city={city}") as resp:
                    if resp.status != 200:
                        await sender.Text(f"""
🔴天气查询失败
错误码：{resp.status}
错误原因：API状态码错误
                    """)
                        return None
                    weather_data = await resp.json()
                    if weather_data['code'] == 200:
                        weather_json = weather_data['data']['current']
                        weather_msg = f"""
⛅天气数据
🏙️当前城市：{weather_json['city']}/{weather_json['cityEnglish']}
⛅当前天气：{weather_json['weather']}/{weather_json['weatherEnglish']}
🧭当前风速：{weather_json['wind']} {weather_json['windSpeed']}
🌡️当前温度：{weather_json['temp']}°C
💦当前湿度：{weather_json['humidity']}
⚖️大气压强：{weather_json['pressure']}
🏭空气指数：{weather_json['air']}(PM2.5指数：{weather_json['air_pm25']})
⏰更新时间：{weather_json['date']} {weather_json['time']}
                        """
                    else:
                        weather_msg = f"""
🔴天气API返回错误
错误码：{weather_data['code']}
错误原因：{weather_data['text']}
请尝试重新获取。
                        """
                    await sender.Text(weather_msg)
        except Exception as e:
            await sender.Text(f"""
🔴天气查询失败
错误原因：{str(e)}
请尝试重新获取。如有问题，请及时上报管理员。
            """)
    
    async def _five_day_weather(self, msg):
        sender = await self._get_adapter_sender()
        try:
            city, error = await self._get_city_name(msg)
            if error:
                await sender.Text(error)
                return
                
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.yyy001.com/api/weather?msg={city}") as resp:
                    if resp.status != 200:
                        await sender.Text(f"""
🔴天气查询失败
错误码：{resp.status}
错误原因：API状态码错误
                    """)
                        return None
                    weather_data = await resp.json()
                    if weather_data['code'] == 200:
                        weather_forecast = weather_data['data']['moji']['data']['forecast']  # 修正了拼写错误: forecasr -> forecast
                        weather_msg = f"⛅{weather_data['data']['moji']['data']['city']} 的未来五日天气预报\n"
                        for i in weather_forecast:
                            weather_msg += f"""
📆{i['date']}   {i['temperature']}
早 {i['dayWeather']} 晚 {i['nightWeather']}
早 {i['windDay']}  晚 {i['windNight']}
湿度 {i['humidity']} 空气质量{i['airQuality']}
                            """           
                    else:
                        weather_msg = f"""
🔴天气API返回错误
错误码：{weather_data['code']}
错误原因：{weather_data['msg']}
请尝试重新获取。
                        """
                    await sender.Text(weather_msg)
        except Exception as e:
            await sender.Text(f"""
🔴天气查询失败
错误原因：{str(e)}
请尝试重新获取。如有问题，请及时上报管理员。
            """)