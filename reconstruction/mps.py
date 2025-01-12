import numpy as np
import scipy.io
import os
import cv2
import scipy.signal

#medfiltParam = 5 # The computed correspondence map is median filtered to mitigate noise. These are the median filter parameters. Usual values are between [1 1] to [7 7], depending on image noise levels, number of images used, and the frequencies.
#Use smaller values of these parameters for low noise levels, large number of input images, and low frequencies. For example, if the average frequency is 64 pixels, and 15 frequencies are used, use medFiltParam = [1 1]. 
#On the other hand, if the average frequency is 16 pixels, and 5 frequencies are used, use medFiltParam = [7 7].

def decode_mps(images_path, pattern_path, prefix="img", suffix=".png", cam=[2048, 2048], pro=[1920, 1080], medfilt_param=5):
    frequency_vec = scipy.io.loadmat(pattern_path + '/freqData.mat')["frequencyVec"][0] #vector containing the projected frequencies (periods in pixels)
    num_frequency = len(frequency_vec)

    # Making the measurement matrix M (see paper for definition)
    M = np.zeros((num_frequency+2, num_frequency+2))
    
    # Filling the first three rows -- correpsonding to the first frequency
    M[0,:3] = [1, np.cos(2*np.pi*0/3), -np.sin(2*np.pi*0/3)]
    M[1,:3] = [1, np.cos(2*np.pi*1/3), -np.sin(2*np.pi*1/3)]
    M[2,:3] = [1, np.cos(2*np.pi*2/3), -np.sin(2*np.pi*2/3)]
    
    # Filling the remaining rows - one for each subsequent frequency
    for f in range(1, num_frequency):
        #print([1, np.zeros(f), 1, np.zeros(numFrequency-f)], M[f+1, :])
        line = [1.0]
        line.extend([0.0]*(f+1))
        line.extend([1.0])
        line.extend([0.0]*(num_frequency-f-1))
        M[f+2, :] = line

    #%%%%%%%%%%%% Making the observation matrix (captured images) %%%%%%%%%%%%%
    R = np.zeros((num_frequency+2, cam[0]*cam[1]))

    # Filling the observation matrix (image intensities)
    for i in range(0, num_frequency+2):
        img_name = images_path + '/' + prefix + "_%03i"%i + suffix
        img = cv2.imread(img_name)   # reads an image in the BGR format
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype("float64")
        img = img / 255
        R[i,:]  = img.T.reshape(-1)
        
    #%%%%%%%%%%%%%%%%%% Solving the linear system %%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # The unknowns are [Offset, Amp*cos(phi_1), Amp*sin(phi_1), Amp*cos(phi_2),
    # ..., Amp*cos(phi_F)], where F = numFrequency. See paper for details. 
    U = np.linalg.solve(M, R)

    # Computing the amplitude 
    Amp = np.sqrt(U[1,:]**2 + U[2,:]**2)

    # Dividing the amplitude to get the CosSinMat --- matrix containing the sin
    # and cos of the phases corresponding to different frequencies. For the
    # phase of the first frequency, we have both sin and cos. For the phases of
    # the remaining frequencies, we have cos. 

    CosSinMat = U[1:, :] / np.tile(Amp, (num_frequency+1, 1))  

    #%%%%%%%%%%%%%% Converting the CosSinMat into column indices %%%%%%%%%%%%%%
    # IC            -- correspondence map (corresponding projector column (sub-pixel) for each camera pixel. Size of IC is the same as input captured imgaes.
    IC = phase_unwrap_cos_sin_to_column_index(CosSinMat, frequency_vec, pro[0], cam[1], cam[0])
    IC = scipy.signal.medfilt2d(IC, medfilt_param) # Applying median filtering

    return IC



# This function converts the CosSinMat into column-correspondence.  
#
# CosSinMat is the matrix containing the sin and cos of the phases 
# corresponding to different frequencies for each camera pixel. For the
# phase of the first frequency, we have both sin and cos. For the phases of
# the remaining frequencies, we have cos. 
#
# The function first performs a linear search on the projector column
# indices. Then, it adds the sub-pixel component. 

def phase_unwrap_cos_sin_to_column_index(CosSinMat, frequencyVec, numProjColumns, nr, nc):
    x0 = np.array([list(range(0, numProjColumns))]) # Projector column indices
    
    # Coomputing the cos and sin values for each projector column. The format 
    # is the same as in CosSinMat - for the phase of the first frequency, we 
    # have both sin and cos. For the phases of the remaining frequencies, we 
    # have cos. These will be compared against the values in CosSinMat to find 
    # the closest match. 

    TestMat = np.tile(x0, (CosSinMat.shape[0], 1)).astype("float64")
    
    TestMat[0,:] = np.cos((np.mod(TestMat[0,:], frequencyVec[0]) / frequencyVec[0]) * 2 * np.pi) # cos of the phase for the first frequency
    TestMat[1,:] = np.sin((np.mod(TestMat[1,:], frequencyVec[0]) / frequencyVec[0]) * 2 * np.pi) # sin of the phase for the first frequency

    for i in range(2, CosSinMat.shape[0]):
        TestMat[i,:] = np.cos((np.mod(TestMat[i,:], frequencyVec[i-1]) / frequencyVec[i-1]) * 2 * np.pi) # cos of the phases of the remaining frequency
        
    #%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    IC = np.zeros((1, nr*nc), dtype="float64") # Vector of column-values
    
    # For each camera pixel, find the closest match
    # TODO parpool(4);% This loop can be run in parallel using MATLAB parallel toolbox. The number here is the number of cores on your machine. 
    for i in range(0, CosSinMat.shape[1]):
        CosSinVec = CosSinMat[:,i]
        CosSinVec = CosSinVec.reshape((CosSinVec.shape[0], 1))
        ErrorVec = np.sum(np.abs(np.tile(CosSinVec, (1, numProjColumns)) - TestMat)**2, axis=0)
        #print(ErrorVec.shape, ErrorVec)
        Ind = np.argmin(ErrorVec)
        #print(Ind)
        IC[0, i] = Ind

    # Computing the fractional value using phase values of the first frequency 
    # since it has both cos and sin values. 

    PhaseFirstFrequency = np.arccos(CosSinMat[0,:]) # acos returns values in [0, pi] range. There is a 2 way ambiguity.
    PhaseFirstFrequency[CosSinMat[1,:]<0] = 2 * np.pi - PhaseFirstFrequency[CosSinMat[1,:]<0] # Using the sin value to resolve the ambiguity
    ColumnFirstFrequency = PhaseFirstFrequency * frequencyVec[0] / (2 * np.pi) # The phase for the first frequency, in pixel units. This is equal to mod(trueColumn, frequencyVec(1)). 

    NumCompletePeriodsFirstFreq = np.floor(IC / frequencyVec[0]) # The number of complete periods for the first frequency 
    ICFrac = NumCompletePeriodsFirstFreq * frequencyVec[0] + ColumnFirstFrequency # The final correspondence, with the fractional component

    # If the difference after fractional correction is large (because of noise), keep the original value. 
    ICFrac[np.abs(ICFrac-IC)>=1] = IC[np.abs(ICFrac-IC)>=1]
    IC = ICFrac

    IC = np.reshape(IC, [nr, nc], order='F')
    return IC
