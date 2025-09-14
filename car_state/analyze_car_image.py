import torch
import torch.nn as nn
from torchvision import transforms
from PIL import Image
import torch.nn.functional as F
import os
import sys

# Импортируем правильную модель
from multiclass_damage_model import MulticlassDamageModel

def load_model(model_path):
    """Загрузка обученной модели"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Используется устройство: {device}")
    
    # Создаем модель с правильной архитектурой
    model = MulticlassDamageModel(num_classes=3)
    
    # Загружаем checkpoint (решаем проблему с PyTorch 2.6)
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
    """Предобработка изображения"""
    # Трансформации как при обучении
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], 
                           std=[0.229, 0.224, 0.225])
    ])
    
    # Загружаем и обрабатываем изображение
    image = Image.open(image_path).convert('RGB')
    image_tensor = transform(image).unsqueeze(0)  # Добавляем batch dimension
    
    return image_tensor, image

def analyze_dirt_level(image):
    """Улучшенный анализ загрязненности автомобиля"""
    import numpy as np
    from PIL import ImageFilter, ImageStat
    
    # Конвертируем в numpy array
    img_array = np.array(image)
    
    # 1. Анализ цветового разнообразия (грязь уменьшает разнообразие)
    # Подсчитываем уникальные цвета в каждом канале
    unique_colors_r = len(np.unique(img_array[:,:,0]))
    unique_colors_g = len(np.unique(img_array[:,:,1])) 
    unique_colors_b = len(np.unique(img_array[:,:,2]))
    color_diversity = (unique_colors_r + unique_colors_g + unique_colors_b) / 3
    
    # 2. Анализ контраста (грязь снижает контраст)
    gray = image.convert('L')
    contrast = ImageStat.Stat(gray).stddev[0]
    
    # 3. Анализ насыщенности цветов
    hsv = image.convert('HSV')
    hsv_array = np.array(hsv)
    saturation = np.mean(hsv_array[:,:,1])  # S канал
    
    # 4. Анализ "коричневых" оттенков (характерно для грязи)
    # Ищем пиксели с коричневыми оттенками
    brown_mask = (
        (img_array[:,:,0] > img_array[:,:,2]) &  # R > B
        (img_array[:,:,1] > img_array[:,:,2]) &  # G > B  
        (img_array[:,:,0] < 150) &  # Не слишком яркий
        (img_array[:,:,1] < 120)    # Приглушенный зеленый
    )
    brown_ratio = np.sum(brown_mask) / (img_array.shape[0] * img_array.shape[1])
    
    # 5. Анализ однородности (грязь делает поверхность более однородной)
    edge_image = gray.filter(ImageFilter.FIND_EDGES)
    edge_intensity = np.mean(np.array(edge_image))
    
    # 6. Анализ общей яркости
    brightness = np.mean(img_array)
    
    # Вычисляем метрики чистоты
    dirt_score = 0
    
    # Низкое цветовое разнообразие = грязь
    if color_diversity < 80:
        dirt_score += 2
    elif color_diversity < 120:
        dirt_score += 1
    
    # Низкий контраст = грязь  
    if contrast < 25:
        dirt_score += 2
    elif contrast < 40:
        dirt_score += 1
    
    # Низкая насыщенность = грязь
    if saturation < 60:
        dirt_score += 1.5
    elif saturation < 100:
        dirt_score += 0.5
    
    # Много коричневых оттенков = грязь
    if brown_ratio > 0.15:
        dirt_score += 2
    elif brown_ratio > 0.08:
        dirt_score += 1
    
    # Слабые края = грязь (замыленность)
    if edge_intensity < 15:
        dirt_score += 1.5
    elif edge_intensity < 25:
        dirt_score += 0.5
    
    # Низкая яркость = возможная грязь
    if brightness < 90:
        dirt_score += 1
    elif brightness < 110:
        dirt_score += 0.5
    
    # Определяем уровень загрязнения
    if dirt_score >= 6:
        return "очень грязная", "�", f"(индекс грязи: {dirt_score:.1f})"
    elif dirt_score >= 4:
        return "грязная", "🟫", f"(индекс грязи: {dirt_score:.1f})"
    elif dirt_score >= 2:
        return "слегка грязная", "🟨", f"(индекс грязи: {dirt_score:.1f})"
    elif dirt_score >= 1:
        return "достаточно чистая", "🟩", f"(индекс грязи: {dirt_score:.1f})"
    else:
        return "очень чистая", "✨", f"(индекс грязи: {dirt_score:.1f})"

def get_human_comment(predicted_class, confidence, dirt_status):
    """Генерирует человечные комментарии о состоянии автомобиля"""
    comments = []
    
    # Комментарии по повреждениям
    if predicted_class == 'no_damage':
        if confidence > 0.8:
            comments.append("Автомобиль в отличном состоянии! 👌")
            comments.append("Видимых повреждений не обнаружено")
        elif confidence > 0.6:
            comments.append("Машина выглядит хорошо, но стоит присмотреться внимательнее")
        else:
            comments.append("Сложно сказать точно - нужен более детальный осмотр")
    
    elif predicted_class == 'minor_damage':
        if confidence > 0.7:
            comments.append("Есть мелкие повреждения - ничего критичного 🔧")
            comments.append("Возможно, пару царапин или небольших вмятин")
            comments.append("Автомобиль пригоден к эксплуатации")
        else:
            comments.append("Похоже на мелкие повреждения, но лучше проверить")
    
    else:  # major_damage
        if confidence > 0.9:
            comments.append("АВТОМОБИЛЬ ПОЛНОСТЬЮ РАЗРУШЕН! ☠️")
            comments.append("Машина НЕ ПРИГОДНА для восстановления")
            comments.append("Рекомендуется утилизация")
        elif confidence > 0.8:
            comments.append("Машина серьезно пострадала! 🚨")
            comments.append("Требуется капитальный ремонт")
            comments.append("Не рекомендуется к эксплуатации без ремонта")
        elif confidence > 0.6:
            comments.append("Серьезные повреждения - нужна экспертиза")
            comments.append("Возможно, дорогостоящий ремонт")
        else:
            comments.append("Подозрение на серьезные повреждения")
    
    # Комментарии по чистоте с деталями
    if "очень грязная" in dirt_status:
        comments.append("Машина в ужасном состоянии по чистоте - невозможно оценить истинные повреждения")
        comments.append("Срочно требуется профессиональная мойка")
    elif "грязная" in dirt_status:
        comments.append("Автомобиль нуждается в хорошей мойке")
        comments.append("Грязь затрудняет точную оценку состояния")
    elif "слегка грязная" in dirt_status:
        comments.append("Небольшая пыль - в целом состояние приемлемое")
    elif "достаточно чистая" in dirt_status:
        comments.append("Автомобиль в хорошем состоянии чистоты")
    else:
        comments.append("Автомобиль идеально чистый - отлично видно все детали")
    
    return comments

def predict_damage(model, image_tensor, device, original_image):
    """Предсказание повреждений с улучшенным анализом чистоты"""
    class_names = ['no_damage', 'minor_damage', 'major_damage']
    
    # Анализируем грязь с новым алгоритмом
    dirt_status, dirt_emoji, dirt_details = analyze_dirt_level(original_image)
    
    with torch.no_grad():
        image_tensor = image_tensor.to(device)
        
        # Получаем предсказания
        outputs = model(image_tensor)
        probabilities = F.softmax(outputs, dim=1)
        confidence, predicted = torch.max(probabilities, 1)
        
        # Конвертируем в numpy для удобства
        probs = probabilities.cpu().numpy()[0]
        predicted_class = class_names[predicted.item()]
        confidence_score = confidence.item()
        
        # Получаем человечные комментарии
        human_comments = get_human_comment(predicted_class, confidence_score, dirt_status)
        
        return predicted_class, confidence_score, probs, class_names, dirt_status, dirt_emoji, human_comments, dirt_details

def analyze_image(image_filename):
    """Анализ изображения по имени файла"""
    # Базовая папка с изображениями
    data_folder = r"C:\Users\Димаш\Desktop\python\hackaton\data"
    model_path = r"C:\Users\Димаш\Desktop\python\hackaton\car_state\training_results\finetuned_best_model.pth"
    
    # Полный путь к изображению
    image_path = os.path.join(data_folder, image_filename)
    
    print("🚗 Анализатор повреждений автомобиля")
    print("="*60)
    print(f"📂 Папка данных: {data_folder}")
    print(f"🖼️  Анализируемое изображение: {image_filename}")
    print("="*60)
    
    # Проверяем существование файлов
    if not os.path.exists(image_path):
        print(f"❌ Изображение не найдено: {image_path}")
        print("\n📋 Доступные изображения в папке:")
        try:
            files = [f for f in os.listdir(data_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp'))]
            for i, file in enumerate(files, 1):
                print(f"   {i}. {file}")
        except:
            print("   Не удалось получить список файлов")
        return
    
    if not os.path.exists(model_path):
        print(f"❌ Модель не найдена: {model_path}")
        return
    
    try:
        # Загружаем модель
        print("📥 Загрузка модели...")
        model, device = load_model(model_path)
        
        # Обрабатываем изображение
        print("🖼️  Обработка изображения...")
        image_tensor, original_image = preprocess_image(image_path)
        print(f"   Размер изображения: {original_image.size}")
        
        # Делаем предсказание
        print("🔍 Анализ повреждений...")
        predicted_class, confidence, probabilities, class_names, dirt_status, dirt_emoji, human_comments, dirt_details = predict_damage(model, image_tensor, device, original_image)
        
        # Выводим результаты
        print("\n" + "="*60)
        print("📊 РЕЗУЛЬТАТЫ АНАЛИЗА:")
        print("="*60)
        
        print(f"🎯 Предсказанный класс: {predicted_class}")
        print(f"📈 Уверенность: {confidence:.1%}")
        print(f"🧼 Чистота: {dirt_emoji} {dirt_status} {dirt_details}")
        
        # Цветовое кодирование по уверенности
        if confidence > 0.8:
            confidence_status = "🟢 Высокая уверенность"
        elif confidence > 0.6:
            confidence_status = "🟡 Средняя уверенность"
        else:
            confidence_status = "🔴 Низкая уверенность"
        
        print(f"📊 Статус: {confidence_status}")
        
        print("\n📋 Детальные вероятности:")
        for name, prob in zip(class_names, probabilities):
            bar_length = int(prob * 30)  # Масштабируем для визуализации
            bar = "█" * bar_length + "░" * (30 - bar_length)
            
            # Эмодзи для классов
            if name == 'no_damage':
                emoji = "✅"
            elif name == 'minor_damage':
                emoji = "🔧"
            else:
                emoji = "🚨"
                
            print(f"   {emoji} {name:15}: {prob:.1%} |{bar}|")
        
        print("\n" + "="*60)
        
        # Человечные комментарии
        print("� ЭКСПЕРТНОЕ ЗАКЛЮЧЕНИЕ:")
        for i, comment in enumerate(human_comments, 1):
            if i == 1:
                print(f"   🔍 {comment}")
            else:
                print(f"   • {comment}")
        
        print("\n" + "="*60)
        
        # Дополнительные рекомендации
        print("💡 РЕКОМЕНДАЦИИ:")
        
        if predicted_class == 'major_damage':
            if confidence > 0.9:
                print("☠️  КРИТИЧЕСКОЕ СОСТОЯНИЕ:")
                print("   • АВТОМОБИЛЬ НЕ ПОДЛЕЖИТ ВОССТАНОВЛЕНИЮ!")
                print("   • Обратиться в страховую для списания")
                print("   • Рассмотреть утилизацию через специализированные центры")
                print("   • НЕ ПЫТАТЬСЯ ЭКСПЛУАТИРОВАТЬ!")
            elif confidence > 0.7:
                print("🚨 КРИТИЧЕСКИЕ ПОВРЕЖДЕНИЯ:")
                print("   • Немедленно прекратить эксплуатацию!")
                print("   • Вызвать эвакуатор")
                print("   • Обязательная экспертиза специалиста")
                print("   • Оценить экономическую целесообразность ремонта")
            else:
                print("⚠️  ПОДОЗРЕНИЕ НА СЕРЬЕЗНЫЕ ПОВРЕЖДЕНИЯ:")
                print("   • Детальный осмотр специалистом")
                print("   • Не рисковать - лучше перестраховаться")
        elif predicted_class == 'minor_damage':
            print("🔧 ПЛАНОВОЕ ОБСЛУЖИВАНИЕ:")
            print("   • Устранить мелкие повреждения в ближайшее время")
            print("   • Проверить лакокрасочное покрытие")
            print("   • Автомобиль можно эксплуатировать")
            print("   • Рекомендуется устранить дефекты для сохранения стоимости")
        elif predicted_class == 'no_damage' and confidence > 0.8:
            print("✅ ОТЛИЧНОЕ СОСТОЯНИЕ:")
            print("   • Поддерживать текущее состояние")
            print("   • Регулярное техническое обслуживание")
            print("   • Автомобиль готов к продаже/использованию")
        
        # Детальные рекомендации по чистоте
        print(f"\n🧼 СОСТОЯНИЕ ЧИСТОТЫ:")
        if "очень грязная" in dirt_status:
            print("   🟤 КРИТИЧЕСКАЯ ЗАГРЯЗНЕННОСТЬ:")
            print("   • Срочная профессиональная мойка и детейлинг")
            print("   • Может потребоваться химчистка салона")
            print("   • Грязь серьезно влияет на оценку повреждений")
            print("   • После мойки - повторный осмотр обязателен")
        elif dirt_status == "грязная":
            print("   🟫 СИЛЬНОЕ ЗАГРЯЗНЕНИЕ:")
            print("   • Рекомендуется комплексная мойка")
            print("   • Использовать профессиональные моющие средства")
            print("   • Обратить внимание на труднодоступные места")
            print("   • После мойки проверить наличие скрытых повреждений")
        elif dirt_status == "слегка грязная":
            print("   🟨 ЛЕГКОЕ ЗАГРЯЗНЕНИЕ:")
            print("   • Обычная мойка справится с загрязнением")
            print("   • Можно помыть самостоятельно")
            print("   • Состояние не критично, но лучше поддерживать чистоту")
        elif dirt_status == "достаточно чистая":
            print("   🟩 ХОРОШАЯ ЧИСТОТА:")
            print("   • Автомобиль в приемлемом состоянии")
            print("   • Легкая мойка для поддержания вида")
            print("   • Отличная видимость для оценки повреждений")
        elif dirt_status == "очень чистая":
            print("   ✨ ИДЕАЛЬНАЯ ЧИСТОТА:")
            print("   • Автомобиль в превосходном состоянии!")
            print("   • Отличная видимость всех деталей")
            print("   • Поддерживать текущий уровень чистоты")
            print("   • Идеальные условия для точной оценки")
        
        if confidence < 0.6:
            print("\n⚠️  ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА:")
            print("   • Сделать фото при хорошем освещении")
            print("   • Убедиться, что автомобиль полностью в кадре")
            print("   • Рассмотреть разные ракурсы")
            print("   • При сомнениях - консультация с экспертом")
        
        print("="*60)
        
    except Exception as e:
        print(f"❌ Ошибка при анализе: {str(e)}")
        import traceback
        traceback.print_exc()

def main():
    """Основная функция с интерактивным вводом"""
    if len(sys.argv) > 1:
        # Имя файла передано как аргумент
        image_filename = sys.argv[1]
    else:
        # Интерактивный ввод
        print("🚗 Анализатор повреждений автомобиля")
        print("="*60)
        image_filename = input("Введите имя файла изображения: ").strip()
        
        if not image_filename:
            print("❌ Имя файла не указано!")
            return
    
    # Анализируем изображение
    analyze_image(image_filename)

if __name__ == "__main__":
    main()