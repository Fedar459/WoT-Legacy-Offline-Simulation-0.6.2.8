# WoT-Legacy-Offline-Simulation-0.6.2.8
Non-commercial technical simulation of the 0.6.2.8 client battle logic for educational purposes.

Based on creations of Imperator Glekobes

Hello, dear developers and players!

The code below represents a standard Offline Hangar modification for the game version 0.6.2.8. It will allow you to view vehicles, modules, the old interface, etc. This modification is non-commercial, and all rights to the "World of Tanks" trademark and game resources belong to Wargaming.net or Lesta Games. The author of the project makes no claim to the intellectual property of these companies.

![Описание изображения](shot_030.jpg)

Download client: https://archive.org/download/world-of-tanks-versions/EU/0.06/World_of_Tanks_0.06.02.08.00_EU_0000_SD.7z

Requires Python 2.6.6 x32 to be compiled and used in the game or extract two files from this archive into your PjOrion folder and change your Python library to python26.dll via Terminal -> Settings -> Basic setup.
________________________________________
[RU] Отказ от ответственности

Данный проект является некоммерческой модификацией для устаревшей версии игры World of Tanks (0.6.2.8), созданной исключительно в ознакомительных и исследовательских целях.
Важные положения:
1.	Этот проект не содержит серверного ПО (BigWorld Server), не эмулирует сетевые протоколы для многопользовательской игры и не позволяет обходить систему авторизации официальных серверов. 
2.	Весь функционал реализован через перехват локальных вызовов клиента (Client-side mocking). Изменения в Login.py позволяют перенаправить логику входа на локальный скрипт Manager.py. 
3.	Отображаемые показатели (золото, кредиты, опыт) являются локальными переменными внутри скрипта и существуют только в рамках текущей сессии оффлайн-режима. Они не имеют связи с реальными аккаунтами или базами данных правообладателей. 
4.	Репозиторий содержит только исходный код модификации на языке Python. Он не включает в себя оригинальные бинарные файлы игры (.exe), текстуры, модели, звуки или иные защищенные авторским правом ресурсы.
5.	Все права на торговую марку "World of Tanks", а также на игровые ресурсы принадлежат компаниям Wargaming.net и/или Lesta Games. Автор проекта не претендует на интеллектуальную собственность указанных компаний.
________________________________________
[EN] Disclaimer

This project is a non-commercial modification for the legacy version of World of Tanks (0.6.2.8), created solely for educational and research purposes.
Key Points:
1.	This project does not contain server-side software (BigWorld Server), does not emulate network protocols for multiplayer, and does not bypass official server authentication. 
2.	All functionality is implemented by mocking client-side calls. Modifications in Login.py redirect the login flow to the local Manager.py script. 
3.	All displayed stats (gold, credits, XP) are local variables within the script and only exist during the offline session. They have no connection to real accounts or official databases. 
4.	This repository contains only the Python source code for the modification. It does not include original game binaries (.exe), textures, models, sounds, or any other copyrighted material.
5.	All rights to the "World of Tanks" trademark and game assets belong to Wargaming.net and/or Lesta Games. The author of this project makes no claim to the intellectual property of these companies.
