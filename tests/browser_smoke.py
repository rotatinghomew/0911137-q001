import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as directory:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'assortment_forecast', '--port', str(port), '--state', str(Path(directory) / 'state.sqlite3')], cwd=ROOT)
        base = f'http://127.0.0.1:{port}'
        try:
            for attempt in range(100):
                try:
                    with urlopen(base + '/api/catalog', timeout=1) as response:
                        catalog = json.load(response)
                    break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError('服务启动超时')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 720})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.wait_for_function("document.querySelector('#status').textContent === '分析已更新'")
                assert page.locator('#results tbody tr').count() > 0
                page.wait_for_function("document.querySelector('#forecast-status').textContent.includes('预测已更新')")
                assert page.locator('#levels tbody tr').count() > 0
                assert page.locator('#series tbody tr').count() > 0
                page.locator('#filters select').select_option(catalog['groups'][0])
                page.get_by_role('button', name='更新分析', exact=True).click()
                page.wait_for_function("document.querySelector('#status').textContent === '分析已更新'")
                page.wait_for_function("document.querySelector('#forecast-status').textContent.includes('预测已更新')")
                assert page.locator('#series tbody tr').count() > 0
                page.locator('#forecast select[name=from]').select_option('p9')
                page.locator('#forecast select[name=to]').select_option('p8')
                page.locator('#forecast input[name=share]').fill('0.5')
                page.get_by_role('button', name='更新预测', exact=True).click()
                page.wait_for_function("document.querySelector('#forecast-status').textContent.includes('预测已更新')")
                assert page.locator('#scenario tbody tr').count() > 0
                assert page.locator('#scenario').inner_text().count('反事实') >= 1
                page.locator('#save input').fill('浏览器业务核对')
                page.get_by_role('button', name='保存当前分析').click()
                page.wait_for_selector('#history button')
                page.locator('#save-forecast input').fill('浏览器补货情景')
                page.get_by_role('button', name='保存当前预测').click()
                page.wait_for_function("document.querySelectorAll('#history button').length >= 2")
                page.reload()
                page.wait_for_selector('#history button')
                page.locator('#history button', has_text='浏览器业务核对').click()
                page.wait_for_function("document.querySelector('#status').textContent.includes('浏览器业务核对')")
                assert page.locator('#filters select').input_value() == catalog['groups'][0]
                page.locator('#history button', has_text='浏览器补货情景').click()
                page.wait_for_function("document.querySelector('#forecast-status').textContent.includes('浏览器补货情景')")
                assert page.locator('#scenario tbody tr').count() > 0
                page.get_by_role('button', name='查看资料').click()
                page.wait_for_selector('#raw tbody tr')
                assert not errors, errors
                print(json.dumps({'结果': '通过', '页面错误': errors, '保存范围': page.locator('#filters select').input_value(), '预测层级行': page.locator('#levels tbody tr').count(), '情景行': page.locator('#scenario tbody tr').count()}, ensure_ascii=False))
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
