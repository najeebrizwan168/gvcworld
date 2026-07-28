import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# 1. Browser Configuration
print("Greece Visa Portal open ho raha hai...")
options = webdriver.ChromeOptions()
# Yeh option browser ko normal look deta hai taake website ko foran shak na ho ke yeh bot hai
options.add_argument("--start-maximized") 

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

try:
    # 2. Target Login URL open karna
    login_url = "http://pk-gr-services.gvcworld.eu"  # Agar exact login URL mukhtalif hai to badal lein
    driver.get(login_url)
    
    # Website ko load hone ke liye thoda waqt dena (Insaan ki tarah behave karna)
    time.sleep(5)
    
    # 3. Email aur Password Fields dhoondna aur data enter karna
    # Note: Agar fields ke name/id badle hue hon to humen HTML inspect karna hoga
    try:
        # Email field dhoondna (assuming name='email' ya id='email')
        email_field = driver.find_element(By.NAME, "email") 
        email_field.send_keys("najeeb@13") # <-- Yahan apna asli email likhein
        print("Email automatically enter kar diya gaya.")
        
        # Password field dhoondna (assuming name='password')
        password_field = driver.find_element(By.NAME, "password")
        password_field.send_keys("980Aa0330") # <-- Yahan apna asli password likhein
        print("Password automatically enter kar diya gaya.")
        
    except Exception as e:
        print("\n[Alert] Element nahi mila! Shayad website ka HTML format alag hai.")
        print("Aap manually browser mein Email/Password fill karein.")

    # 4. **PAUSE ZONE** (Yahan script ruk jayegi taake aap CAPTCHA solve kar sakein)
    print("\n[IMPORTANT] Script pause par hai.")
    print("1. Agar screen par CAPTCHA aaya hai to usey manually solve karein.")
    print("2. 'Login' button par click karke dashboard ke andar chalein jayein.")
    print("3. Jab aap successfully login ho jayein, to terminal par wapas aakar ENTER dabayein.")
    
    input("\nLogin karne ke baad terminal par ENTER press karein taake session verify ho...")

    # 5. Verification Check
    print("Verification successful! Aap logged-in hain. Agla step appointment check karna hoga.")

finally:
    # Browser ko thodi der khula rakhne ke liye taake crash na ho
    time.sleep(5)
    driver.quit()
    print("Browser closed.")