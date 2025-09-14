import torch
import torch.nn as nn
from torchvision import transforms, models
from PIL import Image, ImageFilter, ImageStat
import torch.nn.functional as F
import os
import sys
import numpy as np

class MulticlassDamageModel(nn.Module):
    def __init__(self, num_classes=3, dropout=0.6):
        super().__init__()
        self.backbone = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V2)
        self.backbone.fc = nn.Identity()
        
        self.classifier = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(2048, 1024),
            nn.ReLU(inplace=True),
            nn.BatchNorm1d(1024),
            nn.Dropout(dropout * 0.5),
            nn.Linear(1024, 512),
            nn.ReLU(inplace=True),
            nn.BatchNorm1d(512),
            nn.Dropout(dropout * 0.25),
            nn.Linear(512, num_classes)
        )
        
    def forward(self, x):
        x = self.backbone.conv1(x)
        x = self.backbone.bn1(x)
        x = self.backbone.relu(x)
        x = self.backbone.maxpool(x)
        
        x = self.backbone.layer1(x)
        x = self.backbone.layer2(x)
        x = self.backbone.layer3(x)
        x = self.backbone.layer4(x)
        
        x = self.backbone.avgpool(x)
        x = torch.flatten(x, 1)
        x = self.classifier(x)
        
        return x

def analyze_dirt_level(image):
    """Анализ загрязненности с детальными метриками"""
    img_array = np.array(image)
    
    # Цветовое разнообразие
    unique_colors_r = len(np.unique(img_array[:,:,0]))
    unique_colors_g = len(np.unique(img_array[:,:,1])) 
    unique_colors_b = len(np.unique(img_array[:,:,2]))
    color_diversity = (unique_colors_r + unique_colors_g + unique_colors_b) / 3
    
    # Контраст
    gray = image.convert('L')
    contrast = ImageStat.Stat(gray).stddev[0]
    
    # Насыщенность
    hsv = image.convert('HSV')
    hsv_array = np.array(hsv)
    saturation = np.mean(hsv_array[:,:,1])
    
    # Коричневые оттенки
    brown_mask = (
        (img_array[:,:,0] > img_array[:,:,2]) &
        (img_array[:,:,1] > img_array[:,:,2]) &
        (img_array[:,:,0] < 150) &
        (img_array[:,:,1] < 120)
    )
    brown_ratio = np.sum(brown_mask) / (img_array.shape[0] * img_array.shape[1])
    
    # Четкость краев
    edge_image = gray.filter(ImageFilter.FIND_EDGES)
    edge_intensity = np.mean(np.array(edge_image))
    
    # Яркость
    brightness = np.mean(img_array)
    
    # Подсчет итогового индекса грязи
    dirt_score = 0
    
    if color_diversity < 80:
        dirt_score += 2
    elif color_diversity < 120:
        dirt_score += 1
    
    if contrast < 25:
        dirt_score += 2
    elif contrast < 40:
        dirt_score += 1
    
    if saturation < 60:
        dirt_score += 1.5
    elif saturation < 100:
        dirt_score += 0.5
    
    if brown_ratio > 0.15:
        dirt_score += 2
    elif brown_ratio > 0.08:
        dirt_score += 1
    
    if edge_intensity < 15:
        dirt_score += 1.5
    elif edge_intensity < 25:
        dirt_score += 0.5
    
    if brightness < 90:
        dirt_score += 1
    elif brightness < 110:
        dirt_score += 0.5
    
    # Определяем уровень загрязнения
    if dirt_score >= 6:
        status = "очень грязная"
        emoji = "🟤"
    elif dirt_score >= 4:
        status = "грязная"
        emoji = "🟫"
    elif dirt_score >= 2:
        status = "слегка грязная"
        emoji = "🟨"
    elif dirt_score >= 1:
        status = "достаточно чистая"
        emoji = "🟩"
    else:
        status = "очень чистая"
        emoji = "✨"
    
    metrics = {
        'color_diversity': color_diversity,
        'contrast': contrast,
        'saturation': saturation,
        'brown_ratio': brown_ratio,
        'edge_intensity': edge_intensity,
        'brightness': brightness,
        'dirt_score': dirt_score
    }
    
    return status, emoji, dirt_score, metrics

def determine_repairability(predicted_class, confidence, major_damage_prob):
    """
    Определяет пригодность автомобиля для работы в сервисе такси
    на основе процентных порогов безопасности и стандартов перевозок
    
    Возвращает:
    - repairability_status: "taxi_ready", "conditional_taxi", "repair_required", "taxi_banned"
    - repairability_message: детальное описание для таксопарка
    - economic_assessment: оценка для коммерческого использования
    """
    
    # Более строгие пороги для сервиса такси (безопасность пассажиров приоритет!)
    TAXI_BAN_THRESHOLD = 75.0       # > 75% серьезных повреждений = ЗАПРЕТ на работу в такси
    REPAIR_REQUIRED_THRESHOLD = 50.0 # 50-75% = ОБЯЗАТЕЛЬНЫЙ ремонт перед допуском
    CONDITIONAL_THRESHOLD = 25.0     # 25-50% = условно допустимо с ограничениями
    MINOR_DAMAGE_TAXI_LIMIT = 40.0   # даже мелкие повреждения лимитированы для имиджа
    
    if predicted_class == 'major_damage':
        if confidence > 0.8 and major_damage_prob > TAXI_BAN_THRESHOLD:
            return "taxi_banned", (
                "� АВТОМОБИЛЬ ЗАПРЕЩЕН ДЛЯ РАБОТЫ В ТАКСИ!",
                f"   📊 Вероятность критических повреждений: {major_damage_prob:.1f}%",
                f"   � Превышен предельный порог безопасности ({TAXI_BAN_THRESHOLD}%)",
                "   ⚠️ РИСКИ: Угроза безопасности пассажиров и водителя",
                "   📉 РЕПУТАЦИЯ: Серьезный ущерб имиджу таксопарка",
                "   ⚖️ ПРАВО: Нарушение требований к коммерческим перевозкам",
                "   🎯 РЕШЕНИЕ: Исключить из парка, продать или утилизировать"
            ), "safety_violation"
            
        elif confidence > 0.6 or major_damage_prob > REPAIR_REQUIRED_THRESHOLD:
            return "repair_required", (
                "🔧 ОБЯЗАТЕЛЬНЫЙ РЕМОНТ ПЕРЕД ДОПУСКОМ К РАБОТЕ",
                f"   📊 Вероятность серьезных повреждений: {major_damage_prob:.1f}%",
                f"   ⚖️ Превышен порог допуска к перевозкам ({REPAIR_REQUIRED_THRESHOLD}%)",
                "   � СТАТУС: ВРЕМЕННО ИСКЛЮЧЕН из эксплуатации",
                "   🔧 ТРЕБОВАНИЯ: Капитальный ремонт + техосмотр",
                "   � Ожидаемые затраты: 150-500 тыс. руб.",
                "   📋 Обязательна сертификация после ремонта",
                "   ⏱️ Время простоя: 2-4 недели"
            ), "mandatory_repair"
            
        elif major_damage_prob > CONDITIONAL_THRESHOLD:
            return "conditional_taxi", (
                "⚠️ УСЛОВНО ДОПУСТИМ С ОГРАНИЧЕНИЯМИ",
                f"   📊 Вероятность серьезных повреждений: {major_damage_prob:.1f}%",
                f"   🔶 В пограничной зоне ({CONDITIONAL_THRESHOLD}-{REPAIR_REQUIRED_THRESHOLD}%)",
                "   � ОГРАНИЧЕНИЯ: Только внутригородские поездки",
                "   � ЗАПРЕТ: Междугородние рейсы и VIP-клиенты",
                "   🔍 КОНТРОЛЬ: Еженедельные техосмотры",
                "   💼 СТРАХОВАНИЕ: Повышенные тарифы",
                "   ⏰ ПЛАН: Плановый ремонт в течение месяца"
            ), "restricted_operation"
        else:
            return "conditional_taxi", (
                "🔧 КОСМЕТИЧЕСКИЙ РЕМОНТ РЕКОМЕНДОВАН",
                f"   📊 Вероятность серьезных повреждений: {major_damage_prob:.1f}%",
                "   ✅ Допустимо для работы в такси",
                "   🎨 ИМИДЖ: Желательно устранить видимые дефекты",
                "   💰 Затраты: 50-150 тыс. руб. на косметику",
                "   � РЕЙТИНГ: Поможет поддержать высокие оценки"
            ), "cosmetic_repair"
    
    elif predicted_class == 'minor_damage':
        minor_damage_prob = 100 - major_damage_prob  # примерная оценка
        if confidence > 0.6 and minor_damage_prob > MINOR_DAMAGE_TAXI_LIMIT:
            return "conditional_taxi", (
                "🔧 КОСМЕТИЧЕСКИЙ РЕМОНТ ЖЕЛАТЕЛЕН ДЛЯ ТАКСИ",
                f"   � Заметные косметические дефекты: {minor_damage_prob:.1f}%",
                "   ✅ БЕЗОПАСНОСТЬ: Не влияет на безопасность движения",
                "   � ИМИДЖ: Может снижать рейтинг и привлекательность для клиентов",
                "   💰 Затраты на устранение: 30-100 тыс. руб.",
                "   📱 ОТЗЫВЫ: Возможны негативные комментарии о внешнем виде",
                "   🎯 РЕКОМЕНДАЦИЯ: Плановый косметический ремонт"
            ), "image_improvement"
        else:
            return "taxi_ready", (
                "✅ ПРИГОДЕН ДЛЯ РАБОТЫ В ТАКСИ",
                "   🔧 Минимальные косметические дефекты",
                "   🚗 Полностью пригоден для коммерческих перевозок",
                "   💰 Затраты: 10-50 тыс. руб. на мелкий ремонт",
                "   ⏱️ Время ремонта: 1-3 дня",
                "   🏆 Сохранение хорошего рейтинга сервиса"
            ), "minor_maintenance"
    
    else:  # no_damage
        return "taxi_ready", (
            "🏆 ИДЕАЛЕН ДЛЯ ПРЕМИУМ ТАКСИ-СЕРВИСА",
            "   ✨ Автомобиль в отличном состоянии",
            "   � КЛАСС: Подходит для VIP и бизнес-клиентов",
            "   📈 РЕЙТИНГ: Обеспечит максимальные оценки пассажиров",
            "   💎 ТАРИФЫ: Возможность работы в премиум-сегменте",
            "   🎯 СТАТУС: Эталон качества таксопарка"
        ), "premium_ready"

def generate_expert_assessment(predicted_class, confidence, probabilities, class_names, dirt_status, dirt_score, dirt_metrics):
    """Генерирует экспертное заключение в стиле профессиональной оценки"""
    
    assessment = []
    
    # 1. СОСТОЯНИЕ ПОВРЕЖДЕНИЙ
    assessment.append("┌─────────────────────────────────────────────────────────────────┐")
    assessment.append("│                    🔍 СОСТОЯНИЕ ПОВРЕЖДЕНИЙ                     │")
    assessment.append("└─────────────────────────────────────────────────────────────────┘")
    
    # Заголовок экспертного заключения
    assessment.append("� ЭКСПЕРТНОЕ ЗАКЛЮЧЕНИЕ ДЛЯ СЕРВИСА ТАКСИ")
    assessment.append("=" * 70)
    
    # 1. ОБЩАЯ ОЦЕНКА СОСТОЯНИЯ
    assessment.append("\\n📋 ОБЩАЯ ОЦЕНКА ТЕХНИЧЕСКОГО СОСТОЯНИЯ:")
    
    no_damage_prob = probabilities[0] * 100
    minor_damage_prob = probabilities[1] * 100
    major_damage_prob = probabilities[2] * 100
    
    # Получаем оценку ремонтопригодности
    repairability_status, repairability_msgs, economic_status = determine_repairability(
        predicted_class, confidence, major_damage_prob
    )
    
    if predicted_class == 'no_damage':
        if confidence > 0.85:
            assessment.append("✅ ОТЛИЧНОЕ СОСТОЯНИЕ: Автомобиль находится в превосходном техническом состоянии.")
            assessment.append(f"   Вероятность отсутствия повреждений: {no_damage_prob:.1f}%")
            assessment.append("   Рекомендация: Автомобиль полностью пригоден к эксплуатации и продаже.")
        elif confidence > 0.7:
            assessment.append("✅ ХОРОШЕЕ СОСТОЯНИЕ: Автомобиль в хорошем состоянии с минимальными рисками.")
            assessment.append(f"   Основная вероятность ({no_damage_prob:.1f}%) указывает на отсутствие повреждений")
            assessment.append(f"   Незначительный риск мелких дефектов: {minor_damage_prob:.1f}%")
            assessment.append("   Рекомендация: Автомобиль пригоден к эксплуатации, рекомендуется детальный осмотр.")
        else:
            assessment.append("⚠️ ТРЕБУЕТ ВНИМАНИЯ: Состояние автомобиля неоднозначное.")
            assessment.append(f"   Вероятность отсутствия повреждений: {no_damage_prob:.1f}%")
            assessment.append(f"   Вероятность мелких повреждений: {minor_damage_prob:.1f}%")
            assessment.append("   Рекомендация: ОБЯЗАТЕЛЬНА профессиональная экспертиза перед принятием решений.")
    
    elif predicted_class == 'minor_damage':
        if confidence > 0.8:
            assessment.append("🔧 МЕЛКИЕ ПОВРЕЖДЕНИЯ: Обнаружены незначительные дефекты кузова.")
            assessment.append(f"   Уверенность в диагнозе: {confidence*100:.1f}%")
            assessment.append("   Характер повреждений: Поверхностные царапины, мелкие вмятины, потертости ЛКП")
            assessment.append("   Влияние на безопасность: НЕ КРИТИЧНО для безопасности движения")
            assessment.append("   Влияние на стоимость: Снижение стоимости на 5-15%")
        elif confidence > 0.6:
            assessment.append("🔧 ВЕРОЯТНЫЕ МЕЛКИЕ ПОВРЕЖДЕНИЯ: Высокая вероятность незначительных дефектов.")
            assessment.append(f"   Вероятность мелких повреждений: {minor_damage_prob:.1f}%")
            assessment.append(f"   Альтернативная оценка - без повреждений: {no_damage_prob:.1f}%")
            assessment.append("   Рекомендация: Необходим детальный осмотр для точной оценки характера дефектов.")
        else:
            assessment.append("❓ НЕОПРЕДЕЛЕННОЕ СОСТОЯНИЕ: Требуется дополнительная диагностика.")
            assessment.append("   Система обнаружила признаки возможных повреждений")
            assessment.append("   Рекомендация: ОБЯЗАТЕЛЬНА экспертная оценка специалистом.")
    
    else:  # major_damage
        if confidence > 0.9:
            assessment.append("🚨 КРИТИЧЕСКИЕ ПОВРЕЖДЕНИЯ: Автомобиль серьезно поврежден!")
            assessment.append("   ⚠️ ВНИМАНИЕ: Автомобиль НЕ ПРИГОДЕН для эксплуатации!")
            assessment.append("   Характер повреждений: Значительные деформации кузова, разрушение конструкций")
            assessment.append(f"   Вероятность критических повреждений: {major_damage_prob:.1f}%")
        elif confidence > 0.8:
            assessment.append("🚨 СЕРЬЕЗНЫЕ ПОВРЕЖДЕНИЯ: Обнаружены значительные дефекты кузова.")
            assessment.append(f"   Уверенность в диагнозе: {confidence*100:.1f}%")
            assessment.append("   Характер повреждений: Глубокие вмятины, повреждения панелей, деформации")
            assessment.append("   Влияние на безопасность: МОЖЕТ ВЛИЯТЬ на безопасность движения")
            assessment.append("   Влияние на стоимость: Значительное снижение стоимости (30-70%)")
        elif confidence > 0.6:
            assessment.append("⚠️ ПОДОЗРЕНИЕ НА СЕРЬЕЗНЫЕ ПОВРЕЖДЕНИЯ: Высокий риск значительных дефектов.")
            assessment.append(f"   Вероятность серьезных повреждений: {major_damage_prob:.1f}%")
            assessment.append("   Рекомендация: СРОЧНАЯ профессиональная экспертиза! Не рисковать безопасностью!")
        else:
            assessment.append("❗ ПОТЕНЦИАЛЬНО ОПАСНОЕ СОСТОЯНИЕ: Обнаружены тревожные признаки.")
            assessment.append("   Рекомендация: НЕМЕДЛЕННОЕ обращение к специалисту по кузовному ремонту!")
    
    # 1.1. ОЦЕНКА ПРИГОДНОСТИ ДЛЯ ТАКСИ (НОВАЯ СЕКЦИЯ)
    assessment.append("\\n┌─────────────────────────────────────────────────────────────────┐")
    assessment.append("│            � АНАЛИЗ ПРИГОДНОСТИ ДЛЯ СЕРВИСА ТАКСИ             │")
    assessment.append("└─────────────────────────────────────────────────────────────────┘")
    
    for msg in repairability_msgs:
        assessment.append(msg)
    
    # 2. АНАЛИЗ ЗАГРЯЗНЕННОСТИ
    assessment.append("\\n┌─────────────────────────────────────────────────────────────────┐")
    assessment.append("│                  🧼 АНАЛИЗ СОСТОЯНИЯ ЧИСТОТЫ И УХОДА           │")
    assessment.append("└─────────────────────────────────────────────────────────────────┘")
    
    if dirt_status == "очень грязная":
        assessment.append("🟤 КРИТИЧЕСКОЕ ЗАГРЯЗНЕНИЕ:")
        assessment.append("   • Автомобиль находится в крайне запущенном состоянии")
        assessment.append("   • Толстый слой грязи препятствует точной диагностике повреждений")
        assessment.append("   • Возможно скрытие серьезных дефектов под слоем загрязнений")
        assessment.append("   • СРОЧНАЯ профессиональная мойка и детейлинг обязательны")
        assessment.append("   • После очистки - ПОВТОРНАЯ диагностика необходима")
    elif dirt_status == "грязная":
        assessment.append("🟫 ЗНАЧИТЕЛЬНОЕ ЗАГРЯЗНЕНИЕ:")
        assessment.append("   • Автомобиль нуждается в тщательной очистке")
        assessment.append(f"   • Индекс загрязнения: {dirt_score:.1f}/10 (требует внимания)")
        assessment.append("   • Снижена точность визуальной оценки повреждений")
        assessment.append("   • Рекомендуется комплексная мойка перед детальным осмотром")
        assessment.append("   • Возможны скрытые мелкие дефекты под загрязнениями")
    elif dirt_status == "слегка грязная":
        assessment.append("🟨 УМЕРЕННОЕ ЗАГРЯЗНЕНИЕ:")
        assessment.append("   • Автомобиль в приемлемом состоянии чистоты")
        assessment.append(f"   • Индекс загрязнения: {dirt_score:.1f}/10 (норма)")
        assessment.append("   • Легкий налет пыли не препятствует диагностике")
        assessment.append("   • Рекомендуется обычная мойка для поддержания вида")
        assessment.append("   • Точность оценки повреждений не снижена")
    elif dirt_status == "достаточно чистая":
        assessment.append("🟩 ХОРОШЕЕ СОСТОЯНИЕ ЧИСТОТЫ:")
        assessment.append("   • Автомобиль содержится в хорошем состоянии")
        assessment.append(f"   • Индекс загрязнения: {dirt_score:.1f}/10 (отлично)")
        assessment.append("   • Отличная видимость всех элементов кузова")
        assessment.append("   • Высокая точность диагностики повреждений")
        assessment.append("   • Признак ответственного отношения владельца к автомобилю")
    else:  # очень чистая
        assessment.append("✨ ИДЕАЛЬНОЕ СОСТОЯНИЕ ЧИСТОТЫ:")
        assessment.append("   • Автомобиль в превосходном состоянии ухода")
        assessment.append(f"   • Индекс загрязнения: {dirt_score:.1f}/10 (эталон)")
        assessment.append("   • Идеальная видимость для точной диагностики")
        assessment.append("   • Свидетельствует об отличном техническом обслуживании")
        assessment.append("   • Максимальная сохранность лакокрасочного покрытия")
    
    # 3. ТЕХНИЧЕСКИЕ ХАРАКТЕРИСТИКИ АНАЛИЗА
    assessment.append("\\n┌─────────────────────────────────────────────────────────────────┐")
    assessment.append("│              📊 ТЕХНИЧЕСКИЕ ПАРАМЕТРЫ ДИАГНОСТИКИ              │")
    assessment.append("└─────────────────────────────────────────────────────────────────┘")
    assessment.append(f"   • Цветовое разнообразие: {dirt_metrics['color_diversity']:.1f} (норма: >120)")
    assessment.append(f"   • Контрастность изображения: {dirt_metrics['contrast']:.1f} (норма: >40)")
    assessment.append(f"   • Насыщенность цветов: {dirt_metrics['saturation']:.1f} (норма: >100)")
    assessment.append(f"   • Четкость краев: {dirt_metrics['edge_intensity']:.1f} (норма: >25)")
    assessment.append(f"   • Общая яркость: {dirt_metrics['brightness']:.1f} (норма: >110)")
    
    # 4. ИТОГОВЫЕ РЕКОМЕНДАЦИИ ДЛЯ ТАКСОПАРКА
    assessment.append("\\n┌─────────────────────────────────────────────────────────────────┐")
    assessment.append("│             � ЗАКЛЮЧЕНИЕ ДЛЯ СЕРВИСА ТАКСИ                    │")
    assessment.append("└─────────────────────────────────────────────────────────────────┘")
    
    # Используем новую систему оценки для такси
    if repairability_status == "taxi_ready":
        if economic_status == "premium_ready":
            assessment.append("🏆 РЕКОМЕНДОВАН ДЛЯ ПРЕМИУМ-СЕГМЕНТА:")
            assessment.append("   • ОТЛИЧНОЕ состояние - идеален для VIP-клиентов")
            assessment.append("   • 🌟 Класс обслуживания: Премиум/Бизнес")
            assessment.append("   • 💎 Тарифы: Возможность повышенных тарифов")
            assessment.append("   • 📱 Рейтинг: Гарантированно высокие оценки (4.8-5.0)")
            assessment.append("   • 🎯 СТАТУС: Эталон качества таксопарка")
        else:  # minor_maintenance
            assessment.append("✅ ДОПУЩЕН К РАБОТЕ В ТАКСИ:")
            assessment.append("   • Полностью пригоден для коммерческих перевозок")
            assessment.append("   • 🔧 Минимальный ремонт: 1-3 дня простоя")
            assessment.append("   • 💰 Затраты: 10-50 тыс. руб. на косметику")
            assessment.append("   • 📊 Класс: Стандартный/Комфорт")
            assessment.append("   • 🎯 Готов к работе после мелкого обслуживания")
            
    elif repairability_status == "conditional_taxi":
        if economic_status == "image_improvement":
            assessment.append("🔧 ДОПУЩЕН С РЕКОМЕНДАЦИЕЙ РЕМОНТА:")
            assessment.append("   • Безопасность: НЕ нарушена, можно эксплуатировать")
            assessment.append("   • 📉 ИМИДЖ: Косметические дефекты влияют на привлекательность")
            assessment.append("   • 💰 Плановые затраты: 30-100 тыс. руб.")
            assessment.append("   • 📱 РИСК: Снижение рейтинга из-за внешнего вида")
            assessment.append("   • ⏰ ПЛАН: Косметический ремонт в течение месяца")
        elif economic_status == "cosmetic_repair":
            assessment.append("✅ УСЛОВНО ДОПУЩЕН К РАБОТЕ:")
            assessment.append("   • Можно использовать с косметическими дефектами")
            assessment.append("   • 🎨 Рекомендация: Устранить видимые повреждения")
            assessment.append("   • 💰 Затраты: 50-150 тыс. руб. на внешний вид")
            assessment.append("   • 🏆 ЦЕЛЬ: Поддержание высокого стандарта сервиса")
        else:  # restricted_operation
            assessment.append("⚠️ ОГРАНИЧЕННЫЙ ДОПУСК К РАБОТЕ:")
            assessment.append("   • 🚫 ЗАПРЕТ: VIP-клиенты и междугородние рейсы")
            assessment.append("   • 📍 ОГРАНИЧЕНИЯ: Только городские поездки")
            assessment.append("   • 🔍 КОНТРОЛЬ: Еженедельные технические осмотры")
            assessment.append("   • 💼 СТРАХОВАНИЕ: Повышенные тарифы ОСАГО")
            assessment.append("   • ⏰ СРОК: До капитального ремонта (макс. месяц)")
            
    elif repairability_status == "repair_required":
        assessment.append("� ВРЕМЕННО ИСКЛЮЧЕН - ТРЕБУЕТ РЕМОНТА:")
        assessment.append("   • 🚫 СТАТУС: ЗАПРЕЩЕН к коммерческим перевозкам")
        assessment.append("   • ⚠️ ПРИЧИНА: Угроза безопасности пассажиров")
        assessment.append("   • 🔧 ТРЕБОВАНИЯ: Обязательный капитальный ремонт")
        assessment.append("   • 💰 Затраты: 150-500 тыс. руб.")
        assessment.append("   • 📋 ПРОЦЕДУРА: Ремонт → техосмотр → сертификация")
        assessment.append("   • ⏱️ ПРОСТОЙ: 2-4 недели без доходов")
        assessment.append("   • 💡 РЕШЕНИЕ: Рассмотреть продажу вместо ремонта")
        
    else:  # taxi_banned
        assessment.append("🚫 ИСКЛЮЧЕН ИЗ ТАКСОПАРКА НАВСЕГДА:")
        assessment.append("   • ☠️ ОПАСНОСТЬ: Критическая угроза безопасности")
        assessment.append("   • ⚖️ ПРАВО: Нарушение требований к коммерческим ТС")
        assessment.append("   • 📉 РЕПУТАЦИЯ: Серьезный ущерб имиджу компании")
        assessment.append("   • 💀 СТРАХОВАНИЕ: Полная потеря покрытия")
        assessment.append("   • 🗑️ ЕДИНСТВЕННОЕ РЕШЕНИЕ: Утилизация или продажа на запчасти")
        assessment.append("   • ❌ РЕМОНТ БЕСПОЛЕЗЕН: Невозможно восстановить безопасность")
    
    # Дополнительные рекомендации по чистоте для такси
    if dirt_score > 6.0:
        assessment.append("\\n🧼 ТРЕБОВАНИЯ ПО ЧИСТОТЕ ДЛЯ ТАКСИ:")
        assessment.append("   • 🚨 КРИТИЧНО: Автомобиль слишком грязный для перевозки пассажиров")
        assessment.append("   • 📉 ИМИДЖ: Нарушение стандартов сервиса такси")
        assessment.append("   • 🔧 ДЕЙСТВИЯ: НЕМЕДЛЕННАЯ профессиональная мойка + химчистка")
        assessment.append("   • 💰 Затраты: 3-8 тыс. руб. на детейлинг")
        assessment.append("   • ⏰ СРОК: До выхода на линию максимум 1 день")
    elif dirt_score > 4.0:
        assessment.append("\\n🧼 РЕКОМЕНДАЦИИ ПО ЧИСТОТЕ:")
        assessment.append("   • 📊 Состояние чистоты ниже стандартов такси-сервиса")
        assessment.append("   • 🎯 ДЕЙСТВИЕ: Комплексная мойка перед выходом на линию")
        assessment.append("   • 💰 Затраты: 1.5-3 тыс. руб. на мойку")
        assessment.append("   • 🏆 ЦЕЛЬ: Соответствие имиджу качественного сервиса")
    
    # 5. ПРАВОВАЯ ИНФОРМАЦИЯ ДЛЯ ТАКСОПАРКА
    assessment.append("\\n⚖️ ПРАВОВАЯ ИНФОРМАЦИЯ ДЛЯ КОММЕРЧЕСКИХ ПЕРЕВОЗОК:")
    assessment.append("   • Заключение основано на требованиях безопасности пассажирских перевозок")
    assessment.append("   • ИИ-система анализирует соответствие стандартам такси-сервиса")
    assessment.append("   • Точность модели: F1-score 94.4% (>5000 изображений автомобилей)")
    assessment.append("   • Для официального допуска требуется техосмотр в ГИБДД")
    assessment.append("   • Ответственность за безопасность пассажиров лежит на таксопарке")
    
    assessment.append("\\n" + "=" * 70)
    
    return assessment

def load_model(model_path):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Используется устройство: {device}")
    
    model = MulticlassDamageModel(num_classes=3)
    checkpoint = torch.load(model_path, map_location=device, weights_only=False)
    
    if 'model_state_dict' in checkpoint:
        model.load_state_dict(checkpoint['model_state_dict'])
        print(f"✅ Модель загружена. Эпоха: {checkpoint.get('epoch', 'неизвестно')}")
        if 'f1_score' in checkpoint:
            print(f"📊 F1-score модели: {checkpoint['f1_score']:.4f}")
    else:
        model.load_state_dict(checkpoint)
        print("✅ Модель загружена (старый формат)")
    
    model.to(device)
    model.eval()
    return model, device

def preprocess_image(image_path):
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], 
                           std=[0.229, 0.224, 0.225])
    ])
    
    image = Image.open(image_path).convert('RGB')
    image_tensor = transform(image).unsqueeze(0)
    
    return image_tensor, image

def predict_damage(model, image_tensor, device):
    class_names = ['no_damage', 'minor_damage', 'major_damage']
    
    with torch.no_grad():
        image_tensor = image_tensor.to(device)
        
        outputs = model(image_tensor)
        probabilities = F.softmax(outputs, dim=1)
        confidence, predicted = torch.max(probabilities, 1)
        
        probs = probabilities.cpu().numpy()[0]
        predicted_class = class_names[predicted.item()]
        confidence_score = confidence.item()
        
        return predicted_class, confidence_score, probs, class_names

def analyze_image_expert(image_filename):
    """Экспертный анализ изображения для хакатона"""
    data_folder = r"C:\\Users\\Димаш\\Desktop\\python\\hackaton\\data"
    model_path = r"C:\\Users\\Димаш\\Desktop\\python\\hackaton\\car_state\\training_results\\finetuned_best_model.pth"
    
    image_path = os.path.join(data_folder, image_filename)
    
    print("� СИСТЕМА ИИ-ДИАГНОСТИКИ ДЛЯ СЕРВИСА ТАКСИ")
    print("🏆 Профессиональная оценка пригодности автомобилей для коммерческих перевозок")
    print("="*70)
    print(f"📂 Анализируемый файл: {image_filename}")
    
    if not os.path.exists(image_path):
        print(f"❌ Изображение не найдено: {image_path}")
        return
    
    try:
        print("\\n📥 Инициализация ИИ-модели...")
        model, device = load_model(model_path)
        
        print("🖼️ Предобработка изображения...")
        image_tensor, original_image = preprocess_image(image_path)
        print(f"   Разрешение: {original_image.size[0]}×{original_image.size[1]} пикселей")
        
        print("🔍 Анализ загрязненности...")
        dirt_status, dirt_emoji, dirt_score, dirt_metrics = analyze_dirt_level(original_image)
        
        print("🧠 ИИ-анализ повреждений...")
        predicted_class, confidence, probabilities, class_names = predict_damage(model, image_tensor, device)
        
        # Генерируем экспертное заключение
        expert_report = generate_expert_assessment(
            predicted_class, confidence, probabilities, class_names, 
            dirt_status, dirt_score, dirt_metrics
        )
        
        # Выводим экспертное заключение
        print("\\n" + "="*70)
        print("📝 ЭКСПЕРТНОЕ ЗАКЛЮЧЕНИЕ")
        print("="*70)
        for line in expert_report:
            print(line)
        
        # Краткие технические данные
        print("\\n┌─────────────────────────────────────────────────────────────────┐")
        print("│                📋 КРАТКИЕ ТЕХНИЧЕСКИЕ ДАННЫЕ                   │")
        print("└─────────────────────────────────────────────────────────────────┘")
        print(f"🎯 Предсказанный класс: {predicted_class}")
        print(f"📈 Уверенность модели: {confidence:.1%}")
        print(f"🧼 Состояние чистоты: {dirt_emoji} {dirt_status} (индекс: {dirt_score:.1f})")
        
        print("\\n┌─────────────────────────────────────────────────────────────────┐")
        print("│                📊 РАСПРЕДЕЛЕНИЕ ВЕРОЯТНОСТЕЙ                   │")
        print("└─────────────────────────────────────────────────────────────────┘")
        for name, prob in zip(class_names, probabilities):
            bar_length = int(prob * 30)
            bar = "█" * bar_length + "░" * (30 - bar_length)
            
            if name == 'no_damage':
                emoji = "✅"
                display_name = "Без повреждений"
            elif name == 'minor_damage':
                emoji = "🔧"
                display_name = "Мелкие повреждения"
            else:
                emoji = "🚨"
                display_name = "Серьезные повреждения"
                
            print(f"   {emoji} {display_name:20}: {prob:.1%} |{bar}|")
        
        print("\\n" + "="*70)
        print("✅ Анализ завершен успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка при анализе: {str(e)}")
        import traceback
        traceback.print_exc()

def main():
    if len(sys.argv) > 1:
        image_filename = sys.argv[1]
    else:
        image_filename = input("Введите имя файла изображения: ").strip()
        
        if not image_filename:
            print("❌ Имя файла не указано!")
            return
    
    analyze_image_expert(image_filename)

if __name__ == "__main__":
    main()