import aiohttp
import asyncio
import time
from ErisPulse import sdk

class Main:
    def __init__(self):
        self.sdk = sdk
        self.logger = sdk.logger
        self.adapter = sdk.adapter
        self.data = None
        self.env = sdk.env
        self.pre_bindings = {}  # 预绑定列表 {user_id: {'city': city, 'time': timestamp, 'timer_task': task}}
        self.timeout_seconds = 300  # 5分钟超时
        
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
            self.data = data
            asyncio.create_task(self._handle_request())

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
        elif msg.startswith("强制绑定"):
            return await self._force_bind_user_city(msg)
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
            
            # 如果已有预绑定记录，取消之前的超时任务
            if user_id in self.pre_bindings:
                old_task = self.pre_bindings[user_id].get('timer_task')
                if old_task and not old_task.done():
                    old_task.cancel()
            
            # 创建新的预绑定记录
            timer_task = asyncio.create_task(self._handle_binding_timeout(user_id))
            self.pre_bindings[user_id] = {
                'city': city,
                'time': time.time(),
                'timer_task': timer_task,
                'status': 'pending'  # pending, success, timeout
            }
            
            # 验证城市有效性
            validation_result = await self._validate_city(city)
            if not validation_result["valid"]:
                await sender.Text(
                    f"🔴城市验证失败\n错误原因：{validation_result['message']}\n"
                    f"如果您确认城市没有问题，请在5分钟内使用'/天气 强制绑定 {city}'来强制绑定"
                )
                return
            
            # 验证通过，进行绑定
            self.env.set(f"weather:{user_id}", city)
            self.pre_bindings[user_id]['status'] = 'success'
            await sender.Text(f"成功绑定您的默认城市为: {city}\n以后可以直接使用'/天气 今日'或'/天气 五日'来查询")
            
        except Exception as e:
            await sender.Text(f"绑定城市失败: {str(e)}")
            if user_id in self.pre_bindings:
                self.pre_bindings[user_id]['status'] = 'error'
    
    async def _handle_binding_timeout(self, user_id):
        """处理绑定超时"""
        try:
            await asyncio.sleep(self.timeout_seconds)
            
            # 检查是否仍然处于pending状态
            if user_id in self.pre_bindings and self.pre_bindings[user_id]['status'] == 'pending':
                city = self.pre_bindings[user_id]['city']
                sender = await self._get_adapter_sender()
                try:
                    await sender.Text(f"⚠️您对城市 {city} 的绑定操作已超时(超过5分钟)，请重新使用绑定命令")
                finally:
                    self.pre_bindings[user_id]['status'] = 'timeout'
                    # 不立即删除，保留记录一段时间供强制绑定参考
                    
        except asyncio.CancelledError:
            # 任务被取消是正常情况
            pass
        except Exception as e:
            self.logger.error(f"处理绑定超时出错: {str(e)}")
    
    async def _validate_city(self, city):
        """验证城市是否有效"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.52vmy.cn/api/query/tian?city={city}") as resp:
                    if resp.status != 200:
                        return {
                            "valid": False,
                            "message": f"API状态码错误: {resp.status}"
                        }
                    weather_data = await resp.json()
                    if weather_data['code'] != 200:
                        return {
                            "valid": False,
                            "message": weather_data['text']
                        }
                    return {"valid": True, "message": ""}
        except Exception as e:
            return {
                "valid": False,
                "message": str(e)
            }
    
    async def _force_bind_user_city(self, msg):
        """强制绑定用户城市"""
        sender = await self._get_adapter_sender()
        try:
            user_id = self.data.get("user_id")
            city = msg.replace("强制绑定", "", 1).strip()
            
            # 检查是否有对应的预绑定记录
            if user_id not in self.pre_bindings:
                await sender.Text("⚠️您还没有进行绑定，请先使用指令'/天气 绑定 城市名称'")
                return
                
            pre_binding = self.pre_bindings[user_id]
            
            # 检查城市是否匹配
            if city != pre_binding['city']:
                await sender.Text(f"⚠️您上次尝试绑定的城市是 {pre_binding['city']}，请保持一致")
                return
                
            # 检查状态
            if pre_binding['status'] == 'timeout':
                await sender.Text("⚠️强制绑定已超时(超过5分钟)，请重新使用普通绑定命令")
                return
                
            # 执行强制绑定
            self.env.set(f"weather:{user_id}", city)
            
            # 取消超时任务
            if 'timer_task' in pre_binding and not pre_binding['timer_task'].done():
                pre_binding['timer_task'].cancel()
                
            # 更新状态
            pre_binding['status'] = 'force_success'
            
            await sender.Text(f"⚠️已强制绑定您的默认城市为: {city}\n注意：由于跳过了城市验证，查询时可能出现错误")
            
        except Exception as e:
            await sender.Text(f"强制绑定城市失败: {str(e)}")
    
    async def _cleanup_pre_binding(self, user_id):
        """清理预绑定记录"""
        if user_id in self.pre_bindings:
            # 取消超时任务
            task = self.pre_bindings[user_id].get('timer_task')
            if task and not task.done():
                task.cancel()
            del self.pre_bindings[user_id]
    
    async def _unbind_user_city(self):
        user_id = self.data.get("user_id")
        city = self.env.get(f"weather:{user_id}", "")
        sender = await self._get_adapter_sender()
        try:
            if not city:
                await sender.Text("目前没有绑定城市")
                return
            self.env.delete(f"weather:{user_id}")
            await self._cleanup_pre_binding(user_id)
            await sender.Text(f"成功删除当前绑定的城市：{city}")
        except Exception as e:
            await sender.Text(f"解绑城市失败：{str(e)}")
        
    async def _get_city_name(self, msg):
        """获取城市名称，优先使用传入的，其次使用绑定的"""
        command = msg.split()[0] if msg else ""
        city = msg.replace(command, "", 1).strip()
        
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
                        await sender.Text(f"🔴天气查询失败\n错误码：{resp.status}\n错误原因：API状态码错误")
                        return
                    weather_data = await resp.json()
                    if weather_data['code'] == 200:
                        weather_json = weather_data['data']['current']
                        weather_msg = (
                            f"⛅天气数据\n"
                            f"🏙️当前城市：{weather_json['city']}/{weather_json['cityEnglish']}\n"
                            f"⛅当前天气：{weather_json['weather']}/{weather_json['weatherEnglish']}\n"
                            f"🧭当前风速：{weather_json['wind']} {weather_json['windSpeed']}\n"
                            f"🌡️当前温度：{weather_json['temp']}°C\n"
                            f"💦当前湿度：{weather_json['humidity']}\n"
                            f"⚖️大气压强：{weather_json['pressure']}\n"
                            f"🏭空气指数：{weather_json['air']}(PM2.5指数：{weather_json['air_pm25']})\n"
                            f"⏰更新时间：{weather_json['date']} {weather_json['time']}"
                        )
                    else:
                        weather_msg = (
                            f"🔴天气API返回错误\n"
                            f"错误码：{weather_data['code']}\n"
                            f"错误原因：{weather_data['text']}\n"
                            f"如果这是您的绑定城市，请尝试使用'/天气 解绑'解除绑定，然后重新绑定有效城市"
                        )
                    await sender.Text(weather_msg)
        except Exception as e:
            await sender.Text(f"🔴天气查询失败\n错误原因：{str(e)}\n请尝试重新获取。如有问题，请及时上报管理员。")
    
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
                        await sender.Text(f"🔴天气查询失败\n错误码：{resp.status}\n错误原因：API状态码错误")
                        return
                    weather_data = await resp.json()
                    if weather_data['code'] == 200:
                        weather_forecast = weather_data['data']['moji']['data']['forecast']
                        weather_msg = f"⛅{weather_data['data']['moji']['data']['city']} 的未来五日天气预报\n"
                        for i in weather_forecast:
                            weather_msg += (
                                f"\n📆{i['date']}   {i['temperature']}\n"
                                f"天气：早 {i['dayWeather']}         晚 {i['nightWeather']}\n"
                                f"风速：早 {i['windDay']}  晚 {i['windNight']}\n"
                                f"湿度 {i['humidity']} 空气质量{i['airQuality']}"
                            )           
                    else:
                        weather_msg = (
                            f"🔴天气API返回错误\n"
                            f"错误码：{weather_data['code']}\n"
                            f"错误原因：{weather_data['msg']}\n"
                            f"如果这是您的绑定城市，请尝试使用'/天气 解绑'解除绑定，然后重新绑定有效城市"
                        )
                    await sender.Text(weather_msg)
        except Exception as e:
            await sender.Text(f"🔴天气查询失败\n错误原因：{str(e)}\n请尝试重新获取。如有问题，请及时上报管理员。")