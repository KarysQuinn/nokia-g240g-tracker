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
from pathlib import Path
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

# 配置日志系统
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("modem_tracker.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ConfigLoader:
    """配置加载器"""
    DEFAULT_CONFIG = {
        "username": "admin",
        "password": "FC5B3132",
        "headless": False,
        "base_url": "http://192.168.10.254",
        "debug_dir": "debug",
        "output_file": "device_report.json"
    }

    @classmethod
    def load_config(cls, config_path: str = "config.json") -> dict:
        """从文件加载配置"""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                logger.info("成功加载配置文件")
                return {**cls.DEFAULT_CONFIG, **config}
        except FileNotFoundError:
            logger.warning("配置文件未找到，使用默认配置")
            return cls.DEFAULT_CONFIG
        except json.JSONDecodeError:
            logger.error("配置文件格式错误，使用默认配置")
            return cls.DEFAULT_CONFIG

class DeviceListParser:
    """设备列表解析器"""
    
    @staticmethod
    def parse_device_row(cols: list) -> Optional[Dict]:
        """解析单个设备行"""
        try:
            if len(cols) != 9:
                logger.warning(f"无效的行数据，期望9列，实际{len(cols)}列")
                return None

            device = {
                "status": cols[0].text.strip(),
                "connection_type": cols[1].text.strip(),
                "name": cols[2].text.strip() or "Unknown",
                "ipv4": cols[3].text.strip(),
                "mac": DeviceListParser._format_mac(cols[4].text),
                "allocation": cols[5].text.strip(),
                "lease": DeviceListParser._parse_lease_time(cols[6].text),
                "last_active": DeviceListParser._parse_datetime(cols[7].text),
            }

            device["is_active"] = device["status"].lower() == "active"
            device["is_wireless"] = "wireless" in device["connection_type"].lower()
            return device

        except Exception as e:
            logger.error(f"解析设备行失败: {str(e)}")
            return None

    @staticmethod
    def _format_mac(raw_mac: str) -> str:
        """统一MAC地址格式"""
        mac = re.sub(r"[^A-F0-9]", "", raw_mac.strip().upper())
        return ":".join(mac[i:i+2] for i in range(0, 12, 2)) if len(mac) == 12 else raw_mac

    @staticmethod
    def _parse_lease_time(lease_str: str) -> int:
        """将租约时间转为秒数"""
        time_map = {"hour": 3600, "min": 60, "sec": 1}
        total = 0
        for match in re.finditer(r"(\d+)\s*(hour|min|sec)", lease_str.lower()):
            total += int(match.group(1)) * time_map[match.group(2)]
        return total if total > 0 else lease_str.strip()

    @staticmethod
    def _parse_datetime(dt_str: str) -> str:
        """解析最后活动时间"""
        try:
            return datetime.strptime(dt_str, "%m/%d/%Y %I:%M:%S %p").isoformat()
        except ValueError:
            return dt_str.strip()

class NokiaG240GDeviceTracker:
    def __init__(self, config: dict):
        self.config = config
        self.driver = None
        self._init_browser_options()

    def _init_browser_options(self):
        """初始化浏览器选项"""
        self.options = Options()
        if self.config["headless"]:
            self.options.add_argument("--headless")
        self.options.add_argument("--disable-gpu")
        self.options.add_argument("--window-size=1920,1080")
        self.options.add_argument("--disable-blink-features=AutomationControlled")
        self.options.add_experimental_option("excludeSwitches", ["enable-automation"])

    def init_driver(self):
        """初始化浏览器驱动"""
        logger.info("初始化Edge浏览器...")
        self.driver = webdriver.Edge(options=self.options)
        self._hide_automation_flags()

    def _hide_automation_flags(self):
        """隐藏自动化特征"""
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
                """
            }
        )

    def human_like_input(self, element, text: str):
        """模拟人类输入行为"""
        element.clear()
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.03, 0.15))
        time.sleep(random.uniform(0.1, 0.3))  # 输入后随机等待

    def login(self) -> bool:
        """执行登录流程"""
        try:
            logger.info("导航到登录页面...")
            self.driver.get(f"{self.config['base_url']}/")

            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "form#loginform"))
            )

            logger.info("输入登录凭据...")
            username_field = self.driver.find_element(By.ID, "username")
            password_field = self.driver.find_element(By.ID, "password")

            self.human_like_input(username_field, self.config["username"])
            self.human_like_input(password_field, self.config["password"])

            self.driver.find_element(By.ID, "loginBT").click()

            WebDriverWait(self.driver, 10).until(
                lambda d: d.get_cookie("sid") is not None
            )
            logger.info("登录成功")
            return True

        except Exception as e:
            logger.error(f"登录失败: {str(e)}", exc_info=True)
            self._save_debug_info("login_failure")
            return False

    def get_device_list(self, html_file_path: str) -> Optional[List[Dict]]:
        """从HTML文件解析设备列表"""
        try:
            logger.info(f"解析HTML文件: {html_file_path}")
            with open(html_file_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")

            table = soup.find("tbody", {"id": "devicelist"})
            if not table:
                logger.error("未找到设备列表表格")
                return None

            devices = []
            for row_idx, row in enumerate(table.find_all("tr")):
                cols = row.find_all("td")
                if device := DeviceListParser.parse_device_row(cols):
                    devices.append(device)
                else:
                    logger.warning(f"行 {row_idx} 解析失败")

            logger.info(f"成功解析 {len(devices)} 个设备")
            return devices

        except Exception as e:
            logger.error(f"解析HTML文件失败: {str(e)}", exc_info=True)
            self._save_debug_info("parse_failure")
            return None

    def _save_debug_info(self, scenario: str):
        """保存调试信息"""
        try:
            debug_dir = Path(self.config["debug_dir"])
            debug_dir.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.driver.save_screenshot(debug_dir / f"{scenario}_{timestamp}.png")
            
            with open(debug_dir / f"{scenario}_{timestamp}.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logger.error(f"保存调试信息失败: {str(e)}")

    def close(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            logger.info("浏览器已关闭")

class ReportGenerator:
    """报告生成器"""
    
    @staticmethod
    def print_summary(devices: List[Dict]):
        """打印摘要信息"""
        print("\n设备列表摘要：")
        print("-" * 90)
        print(f"{'状态':<8}{'设备名称':<20}{'IP地址':<15}{'MAC地址':<20}{'最后活动时间':<25}")
        print("-" * 90)
        for device in devices:
            print(
                f"{device['status']:<8}"
                f"{device['name']:<20}"
                f"{device['ipv4']:<15}"
                f"{device['mac']:<20}"
                f"{device['last_active']:<25}"
            )

    @staticmethod
    def save_report(devices: List[Dict], filename: str):
        """保存完整报告"""
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(devices, f, indent=2, ensure_ascii=False)
        logger.info(f"完整报告已保存至 {filename}")

def main():
    config = ConfigLoader.load_config()
    tracker = NokiaG240GDeviceTracker(config)
    
    try:
        tracker.init_driver()
        if tracker.login():
            if devices := tracker.get_device_list("debug/device_list_sample.html"):
                ReportGenerator.print_summary(devices)
                ReportGenerator.save_report(devices, config["output_file"])
            else:
                logger.warning("未获取到有效设备数据")
    except Exception as e:
        logger.critical(f"主流程异常: {str(e)}", exc_info=True)
    finally:
        tracker.close()

if __name__ == "__main__":
    main()
