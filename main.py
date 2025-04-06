from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options
from datetime import datetime
import re
import time
import json
import logging
import random

# 配置日志系统
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("modem_tracker.log"), logging.StreamHandler()],
)


class NokiaG240GDeviceTracker:
    def __init__(self, headless=False):
        self.options = Options()
        if headless:
            self.options.add_argument("--headless")
        self.options.add_argument("--disable-gpu")
        self.options.add_argument("--window-size=1920,1080")
        self.options.add_argument("--disable-blink-features=AutomationControlled")
        self.driver = None
        self.base_url = "http://192.168.10.254"

    def init_driver(self):
        """初始化浏览器驱动"""
        logging.info("初始化Edge浏览器...")
        self.driver = webdriver.Edge(options=self.options)
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                """
            },
        )

    def human_like_input(self, element, text):
        """模拟人类输入行为"""
        element.clear()
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.05, 0.2))
        time.sleep(0.3)

    def login(self, username, password):
        """执行登录流程"""
        try:
            logging.info("导航到登录页面...")
            self.driver.get(f"{self.base_url}/")

            # 等待登录表单加载
            form_locator = (By.CSS_SELECTOR, "form#loginform")
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located(form_locator)
            )

            logging.info("输入登录凭据...")
            username_field = self.driver.find_element(By.ID, "username")
            password_field = self.driver.find_element(By.ID, "password")

            self.human_like_input(username_field, username)
            self.human_like_input(password_field, password)

            # 提交表单
            self.driver.find_element(By.ID, "loginBT").click()

            # 验证登录成功
            WebDriverWait(self.driver, 10).until(
                lambda d: d.get_cookie("sid") is not None
            )
            logging.info("登录成功")
            return True

        except Exception as e:
            logging.error(f"登录失败: {str(e)}", exc_info=True)
            self._save_debug_info("login_failure")
            return False

    def get_device_list(self, html_file_path):
        """从HTML文件解析设备列表"""
        try:
            logging.info(f"解析HTML文件: {html_file_path}")
            with open(html_file_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_content, "html.parser")

            # 定位设备列表表格
            table = soup.find("tbody", {"id": "devicelist"})
            if not table:
                logging.error("未找到设备列表表格")
                return None

            rows = table.find_all("tr")
            logging.info(f"发现 {len(rows)} 个设备条目")

            devices = []
            for index, row in enumerate(rows):
                try:
                    cols = row.find_all("td")
                    if len(cols) != 9:
                        logging.warning(f"行 {index} 列数异常: {len(cols)}")
                        continue

                    device = self._parse_device_row_from_html(cols)
                    devices.append(device)

                except Exception as e:
                    logging.warning(f"解析行 {index} 失败: {str(e)}")
                    continue

            return devices

        except Exception as e:
            logging.error(f"解析HTML文件失败: {str(e)}", exc_info=True)
            return None

    def _parse_device_row_from_html(self, cols):
        """从HTML列解析单个设备行"""
        device = {
            "status": cols[0].text.strip(),
            "connection_type": cols[1].text.strip(),
            "name": cols[2].text.strip() or "Unknown",
            "ipv4": cols[3].text.strip(),
            "mac": self._format_mac(cols[4].text),
            "allocation": cols[5].text.strip(),
            "lease": self._parse_lease_time(cols[6].text),
            "last_active": self._parse_datetime(cols[7].text),
        }

        device["is_active"] = device["status"].lower() == "active"
        device["is_wireless"] = "wireless" in device["connection_type"].lower()

        return device

    def _parse_device_row(self, cols):
        """解析单个设备行"""
        # 基础字段解析
        device = {
            "status": cols[0].text.strip(),
            "connection_type": cols[1].text.strip(),
            "name": cols[2].text.strip() or "Unknown",
            "ipv4": cols[3].text.strip(),
            "mac": self._format_mac(cols[4].text),
            "allocation": cols[5].text.strip(),
            "lease": self._parse_lease_time(cols[6].text),
            "last_active": self._parse_datetime(cols[7].text),
        }

        # 附加处理
        device["is_active"] = device["status"].lower() == "active"
        device["is_wireless"] = "wireless" in device["connection_type"].lower()

        return device

    def _format_mac(self, raw_mac):
        """统一MAC地址格式"""
        mac = raw_mac.strip().upper()
        mac = re.sub(r"[^A-F0-9]", "", mac)  # 移除非十六进制字符
        return (
            ":".join(mac[i : i + 2] for i in range(0, 12, 2))
            if len(mac) == 12
            else raw_mac
        )

    def _parse_lease_time(self, lease_str):
        """将租约时间转为秒数"""
        try:
            time_map = {"hour": 3600, "min": 60, "sec": 1}
            total = 0
            for match in re.finditer(r"(\d+)\s*(hour|min|sec)", lease_str):
                value, unit = match.groups()
                total += int(value) * time_map[unit.lower()]
            return total
        except:
            return lease_str.strip()

    def _parse_datetime(self, dt_str):
        """解析最后活动时间"""
        try:
            return datetime.strptime(dt_str, "%m/%d/%Y %I:%M:%S %p").isoformat()
        except ValueError:
            return dt_str.strip()

    def _save_debug_info(self, scenario):
        """保存调试信息"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            # 保存截图
            self.driver.save_screenshot(f"debug/{scenario}_{timestamp}.png")
            # 保存页面源码
            with open(f"debug/{scenario}_{timestamp}.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error(f"保存调试信息失败: {str(e)}")

    def close(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            logging.info("浏览器已关闭")


def main():
    # 配置参数
    config = {"username": "admin", "password": "FC5B3132", "headless": False}
    html_file_path = "debug/device_list_failure_20250406_160431.html"

    # 初始化跟踪器
    tracker = NokiaG240GDeviceTracker(headless=config["headless"])
    try:
        tracker.init_driver()

        if tracker.login(config["username"], config["password"]):
            # 从HTML文件获取设备列表
            devices = tracker.get_device_list(html_file_path)
            if devices:
                # 打印摘要信息
                print("\n设备列表摘要：")
                print("-" * 120)
                print(
                    f"{'状态':<8}{'设备名称':<20}{'IP地址':<15}{'MAC地址':<20}{'最后活动时间':<25}"
                )
                print("-" * 120)
                for device in devices:
                    print(
                        f"{device['status']:<8}"
                        f"{device['name']:<20}"
                        f"{device['ipv4']:<15}"
                        f"{device['mac']:<20}"
                        f"{device['last_active']:<25}"
                    )

                # 保存完整报告
                with open("device_report.json", "w", encoding="utf-8") as f:
                    json.dump(devices, f, indent=2, ensure_ascii=False)
                logging.info("完整报告已保存至 device_report.json")
            else:
                logging.warning("未获取到有效设备数据")
        else:
            logging.error("登录失败，请检查日志")

    except Exception as e:
        logging.critical(f"主流程异常: {str(e)}", exc_info=True)
    finally:
        tracker.close()


if __name__ == "__main__":
    main()
