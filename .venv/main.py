import socket
import os
import threading
import time
from tkinter import filedialog, Tk
import concurrent.futures
import requests
import logging
from requests.auth import HTTPProxyAuth
from rich.console import Console
from rich.table import Table
from rich.progress import Progress
from requests import get

# Настройка логирования (убираем DEBUG)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler("proxy_checker.log"), logging.StreamHandler()])

console = Console()

# Функция для определения страны по IP
def get_country_by_ip(ip):
    try:
        response = get(f"https://ipapi.co/{ip}/json/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data.get("country_name", "Unknown")
        else:
            return "Unknown"
    except Exception as e:
        logging.error(f"Error fetching country for IP {ip}: {str(e)}")
        return "Unknown"

# Функция проверки прокси (поддерживает приватные прокси с авторизацией)
def check_proxy(proxy, results, index, destination_url):
    try:
        proxy = proxy.strip()

        if proxy.count(":") == 3:
            # Обработка формата приватного прокси ip:port:user:password
            parts = proxy.split(":")
            ip, port, user, password = parts[0], parts[1], parts[2], parts[3]
            proxies = {
                "http": f"http://{user}:{password}@{ip}:{port}",
                "https": f"http://{user}:{password}@{ip}:{port}",
            }
            auth = HTTPProxyAuth(user, password)
        else:
            # Обработка стандартного формата прокси ip:port
            ip, port = proxy.split(":")
            if not proxy.startswith("http://") and not proxy.startswith("https://"):
                proxy = "http://" + proxy
            proxies = {
                "http": proxy,
                "https": proxy,
            }
            auth = None

        # Проверка доступности прокси через сокет
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)  # Устанавливаем таймаут в 2 секунды
        try:
            start_time = time.perf_counter()
            sock.connect((ip, int(port)))
            sock.close()
            socket_ping_time = (time.perf_counter() - start_time) * 1000  # Измеряем точное время отклика через сокет
        except socket.error:
            results[index] = [proxy, 'N/A', 'Unreachable', 'N/A']
            logging.warning(f'Proxy {proxy} is unreachable via socket connection')
            return

        # Используем requests для более точного измерения времени пинга через HTTP(S)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "*/*",
            "Connection": "keep-alive"
        }
        total_http_ping_time = 0
        successful_requests = 0

        try:
            for attempt in range(5):  # Увеличиваем количество попыток для повышения точности
                start_time = time.perf_counter()
                response = requests.get(destination_url, proxies=proxies, timeout=10, headers=headers, auth=auth)
                if response.status_code == 200:
                    ping_time = (time.perf_counter() - start_time) * 1000 - 10  # Корректируем значение на 10 мс для повышения точности
                    total_http_ping_time += ping_time
                    successful_requests += 1
        except requests.exceptions.RequestException as e:
            logging.warning(f'Proxy {proxy} HTTP request failed: {str(e)}')

        if successful_requests > 0:
            avg_http_ping_time = total_http_ping_time / successful_requests
        else:
            avg_http_ping_time = float('inf')  # Если HTTP-запросы не удались, установить большое значение

        # Усреднение Socket и HTTP пинга для более точного результата
        avg_ping_time = round((socket_ping_time + avg_http_ping_time) / 2, 2) if successful_requests > 0 else 'inf'

        if successful_requests > 0 and avg_ping_time != 'inf':
            country = get_country_by_ip(ip)
            results[index] = [proxy, f'{avg_ping_time:.2f} ms', 'Working', country]
            logging.info(f'Proxy {proxy} is working with average ping {avg_ping_time:.2f} ms')
        else:
            results[index] = [proxy, 'N/A', 'Unreachable or Authentication Issue', 'N/A']
    except ValueError as ve:
        results[index] = [proxy, 'N/A', 'Invalid Format', 'N/A']
        logging.error(f'Proxy {proxy} error: {ve}')
    except Exception as e:
        results[index] = [proxy, 'N/A', f'Error: {str(e)}', 'N/A']
        logging.error(f'Proxy {proxy} error: {str(e)}')
    finally:
        if results[index] is None:
            results[index] = [proxy, 'N/A', 'Unknown Error', 'N/A']

# Загрузка прокси из файла
def load_proxies_from_file():
    Tk().withdraw()
    file_path = filedialog.askopenfilename(title="Select file with proxies")
    if file_path:
        with open(file_path, 'r') as file:
            return [line.strip() for line in file if line.strip()]
    return []


# Функция обновления прогресса проверки прокси
def update_progress(results, proxies, stop_event):
    while not stop_event.is_set():
        os.system('cls' if os.name == 'nt' else 'clear')
        table = Table(title="Proxy Checker Results", show_lines=True)
        table.add_column("Proxy", justify="left", style="cyan", no_wrap=True)
        table.add_column("Ping (ms)", justify="right", style="green", highlight=True)
        table.add_column("Status", justify="left", style="magenta")
        table.add_column("Country", justify="left", style="yellow")

        for idx, proxy in enumerate(proxies):
            if results[idx] is None:
                table.add_row(proxy, "Calculating...", "Checking...", "N/A")
            else:
                ping_value = results[idx][1]
                if ping_value == 'inf ms':
                    table.add_row(results[idx][0], '[yellow]inf[/yellow] ms', results[idx][2], results[idx][3])
                else:
                    table.add_row(*results[idx])

        console.print(table)
        time.sleep(1)


# Функция для запуска тестирования прокси
def run_proxy_tests(proxies, results, destination_url):
    stop_event = threading.Event()
    progress_thread = threading.Thread(target=update_progress, args=(results, proxies, stop_event))
    progress_thread.start()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_index = {executor.submit(check_proxy, proxy, results, idx, destination_url): idx for idx, proxy in
                           enumerate(proxies)}

        with Progress() as progress:
            task = progress.add_task("Checking proxies...", total=len(proxies))
            for future in concurrent.futures.as_completed(future_to_index):
                try:
                    future.result()
                except Exception as exc:
                    logging.error(f'Proxy check generated an exception: {exc}')
                progress.update(task, advance=1)

    stop_event.set()
    progress_thread.join()


# Основная функция
def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    ascii_art = r"""
    ______    ____    __     ____    _   __   _____    ____       
   / ____/   /  _/   / /    /  _/   / | / /  / ___/   /  _/       
  / /_       / /    / /     / /    /  |/ /   \__ \    / /         
 / __/     _/ /    / /___ _/ /    / /|  /   ___/ /  _/ /          
/_/       /___/   /_____//___/   /_/ |_/   /____/  /___/    
                    Proxy Checker v1.7.1     
    """
    console.print(ascii_art, style="bold blue")

    # Выбор целевого URL
    console.print("Select the destination URL for proxy testing:")
    console.print("1: [bold]http://google.com[/bold]")
    console.print("2: [bold]http://httpbin.org/get[/bold]")
    console.print("3: [bold]http://azenv.net[/bold]")
    console.print("4: [bold]Enter your own URL[/bold]")

    destination_choice = input("Enter your choice (1, 2, 3): ")
    if destination_choice == '1':
        destination_url = "http://google.com"
    elif destination_choice == '2':
        destination_url = "http://httpbin.org/get"
    elif destination_choice == '4':
        destination_url = input("Enter your own URL (e.g., http://example.com): ")
    elif destination_choice == '3':
        destination_url = "http://azenv.net"
    else:
        console.print("[red]Invalid choice! Defaulting to http://httpbin.org/get.[/red]")
        destination_url = "http://httpbin.org/get"

    while True:
        # Выбор между проверкой прокси и мониторингом
        console.print("\nSelect mode:")
        console.print("1: [bold]One-time proxy check[/bold]")
        console.print("2: [bold]Monitor proxies[/bold]")

        mode_choice = input("Enter your choice (1 or 2): ")

        console.print("\n1: [bold]Load proxies from file[/bold]")
        console.print("2: [bold]Enter proxy manually[/bold]")

        choice = input("Select an option (1, 2): ")
        proxies = []

        if choice == '1':
            proxies = load_proxies_from_file()
            if not proxies:
                console.print("[red]No proxies loaded. Returning to menu...[/red]")
                continue
        elif choice == '2':
            proxy = input("Enter proxy (format: ip:port or ip:port:user:password): ")
            proxies.append(proxy)
        else:
            console.print("[red]Invalid choice![/red]")
            continue

        results = [None] * len(proxies)

        if mode_choice == '1':
            # Одноразовая проверка прокси
            run_proxy_tests(proxies, results, destination_url)
        elif mode_choice == '2':
            # Режим мониторинга прокси
            interval = input("Enter the monitoring interval in seconds: ")
            try:
                interval = int(interval)
                while True:
                    run_proxy_tests(proxies, results, destination_url)
                    console.print(f"\n[cyan]Monitoring will continue in {interval} seconds...[/cyan]")
                    console.print(
                        "[bold yellow]Press CTRL+C to stop monitoring and return to the main menu.[/bold yellow]")
                    time.sleep(interval)
            except ValueError:
                console.print("[red]Invalid interval. Please enter a numeric value.[/red]")
                continue
            except KeyboardInterrupt:
                console.print("\n[red]Monitoring stopped. Returning to the main menu...[/red]")
        else:
            console.print("[red]Invalid choice for mode![/red]")
            continue

        # Отображение результатов
        os.system('cls' if os.name == 'nt' else 'clear')
        console.print(ascii_art, style="bold blue")
        table = Table(title="Final Proxy Checker Results", show_lines=True)
        table.add_column("Proxy", justify="left", style="cyan", no_wrap=True)
        table.add_column("Ping (ms)", justify="right", style="green")
        table.add_column("Status", justify="left", style="magenta")
        table.add_column("Country", justify="left", style="yellow")

        working_proxies = []
        for result in results:
            table.add_row(*result)
            if result[2].lower() == 'working':
                working_proxies.append(result[0])

        console.print(table)

        # Сохранение рабочих прокси
        if working_proxies:
            with open("working_proxies.txt", "w") as file:
                stripped_proxies = [proxy.replace('http://', '').replace('https://', '') for proxy in working_proxies]
                file.write("\n".join(stripped_proxies))
            console.print("\n[green]Working proxies have been saved to working_proxies.txt[/green]")

        if mode_choice == '1':
            # Вопрос о повторной проверке для одноразового режима
            retest_choice = input("\nDo you want to check more proxies? (y/n): ")
            if retest_choice.lower() not in ['y']:
                break
        else:
            # Если в режиме мониторинга, программа просто продолжает работать
            break


if __name__ == '__main__':
    main()
