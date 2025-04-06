from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options
import json
import logging
import re
import time
from datetime import datetime


class NokiaG240GDeviceTracker:
    def __init__(self, headless=False):
        self.options = Options()
        if headless:
            self.options.add_argument("--headless")
        self.options.add_argument("--disable-gpu")
        self.options.add_argument("--window-size=1920,1080")
        self.driver = None
        self.base_url = "http://192.168.10.254"

        # 配置日志
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("modem_tracker.log"),
                logging.StreamHandler(),
            ],
        )

    def init_driver(self):
        """初始化浏览器驱动"""
        logging.info("Initializing Edge browser...")
        self.driver = webdriver.Edge(options=self.options)
        # 禁用自动化检测特征
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

    def login(self, username, password):
        """执行登录流程"""
        try:
            logging.info("Navigating to login page...")
            self.driver.get(f"{self.base_url}/")

            # 等待登录表单加载
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.ID, "loginform"))
            )

            # 输入凭据
            username_field = self.driver.find_element(By.ID, "username")
            password_field = self.driver.find_element(By.ID, "password")

            username_field.clear()
            username_field.send_keys(username)
            time.sleep(1)
            password_field.clear()
            password_field.send_keys(password)
            time.sleep(1)

            # 提交表单
            self.driver.find_element(By.ID, "loginBT").click()

            # 验证登录成功
            WebDriverWait(self.driver, 10).until(
                lambda d: d.get_cookie("sid") is not None
            )
            logging.info("Login successful")
            return True

        except Exception as e:
            logging.error(f"Login failed: {str(e)}", exc_info=True)
            self._save_debug_info("login_failure")
            return False

    def get_device_list(self):
        """获取设备列表（从JS变量直接提取）"""
        try:
            logging.info("Extracting device data from JavaScript...")

            # 获取原始设备数据
            devices_js = self.driver.execute_script("return JSON.stringify(device_cfg)")
            devices_data = json.loads(devices_js)

            # 格式化设备信息
            formatted_devices = []
            for device in devices_data:
                formatted_devices.append(
                    {
                        "status": "Active" if device.get("Active") else "Inactive",
                        "connection_type": device.get("InterfaceType", "Unknown"),
                        "name": device.get("HostName", "Unknown"),
                        "ip_address": device.get("IPAddress", ""),
                        "mac_address": self._format_mac(device.get("MACAddress", "")),
                        "allocation": device.get("AddressSource", "Unknown"),
                        "lease_remaining": device.get("LeaseTimeRemaining", 0),
                        "last_active": device.get("X_ALU_COM_LastActiveTime", ""),
                    }
                )

            logging.info(f"Found {len(formatted_devices)} devices")
            return formatted_devices

        except Exception as e:
            logging.error(f"Failed to extract devices: {str(e)}")
            self._save_debug_info("device_extract_failure")

            # 回退到DOM解析
            logging.info("Attempting DOM fallback...")
            return self._get_devices_from_dom()

    def _get_devices_from_dom(self):
        """从DOM表格获取设备列表（备用方案）"""
        try:
            self.driver.get(f"{self.base_url}/lan_status.cgi?wlan")
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.ID, "devicelist"))
            )

            devices = []
            rows = self.driver.find_elements(By.CSS_SELECTOR, "#devicelist tr")

            for row in rows[1:]:  # 跳过表头
                cols = row.find_elements(By.TAG_NAME, "td")
                if len(cols) < 8:
                    continue

                devices.append(
                    {
                        "status": cols[0].text.strip(),
                        "connection_type": cols[1].text.strip(),
                        "name": cols[2].text.strip(),
                        "ip_address": cols[3].text.strip(),
                        "mac_address": self._format_mac(cols[4].text),
                        "allocation": cols[5].text.strip(),
                        "lease_remaining": self._parse_lease_time(cols[6].text),
                        "last_active": cols[7].text.strip(),
                    }
                )

            logging.info(f"DOM fallback found {len(devices)} devices")
            return devices

        except Exception as e:
            logging.error(f"DOM fallback failed: {str(e)}")
            return None

    def _format_mac(self, raw_mac):
        """统一MAC地址格式"""
        if not raw_mac:
            return ""
        mac = re.sub(r"[^0-9A-Fa-f]", "", raw_mac)
        return (
            ":".join(mac[i : i + 2] for i in range(0, 12, 2))
            if len(mac) == 12
            else raw_mac
        )

    def _parse_lease_time(self, lease_str):
        """将租约时间转为秒数"""
        try:
            total = 0
            time_map = {"hour": 3600, "min": 60, "sec": 1}
            for match in re.finditer(
                r"(\d+)\s*(hour|min|sec)", lease_str, re.IGNORECASE
            ):
                value, unit = match.groups()
                total += int(value) * time_map[unit.lower()]
            return total
        except:
            return lease_str.strip()

    def _save_debug_info(self, scenario):
        """保存调试信息"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            # 保存截图
            self.driver.save_screenshot(f"debug/{scenario}_{timestamp}.png")
            # 保存页面源码
            with open(f"debug/{scenario}_{timestamp}.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            # 保存JS上下文
            with open(f"debug/{scenario}_{timestamp}.json", "w") as f:
                f.write(self.driver.execute_script("return JSON.stringify(window)"))
        except Exception as e:
            logging.error(f"Could not save debug info: {str(e)}")

    def close(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            logging.info("Browser closed")


def main():
    tracker = NokiaG240GDeviceTracker(headless=False)
    try:
        tracker.init_driver()

        if tracker.login("admin", "FC5B3132"):
            devices = tracker.get_device_list()
            if devices:
                print("\nConnected Devices:")
                print("-" * 120)
                print(
                    f"{'Status':<8}{'Name':<20}{'IP Address':<15}{'MAC Address':<20}{'Type':<12}{'Last Active':<25}"
                )
                print("-" * 120)
                for device in devices:
                    print(
                        f"{device['status']:<8}"
                        f"{device['name']:<20}"
                        f"{device['ip_address']:<15}"
                        f"{device['mac_address']:<20}"
                        f"{device['connection_type']:<12}"
                        f"{device['last_active']:<25}"
                    )

                # 保存完整报告
                with open("device_report.json", "w") as f:
                    json.dump(devices, f, indent=2)
                logging.info("Report saved to device_report.json")
            else:
                logging.warning("No devices found")
        else:
            logging.error("Login failed")

    except Exception as e:
        logging.critical(f"Fatal error: {str(e)}", exc_info=True)
    finally:
        tracker.close()


if __name__ == "__main__":
    main()
